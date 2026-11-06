import React from 'react';
import {Link as RouterLink, useNavigate, useParams} from 'react-router-dom';

import ApprovedIcon from '@mui/icons-material/CheckCircleOutlineOutlined';
import AppRequestIcon from '@mui/icons-material/AddBox';
import PendingIcon from '@mui/icons-material/HelpOutlineOutlined';
import RejectedIcon from '@mui/icons-material/HighlightOff';
import Timeline from '@mui/lab/Timeline';
import TimelineConnector from '@mui/lab/TimelineConnector';
import TimelineContent from '@mui/lab/TimelineContent';
import TimelineDot from '@mui/lab/TimelineDot';
import TimelineItem from '@mui/lab/TimelineItem';
import TimelineOppositeContent, {timelineOppositeContentClasses} from '@mui/lab/TimelineOppositeContent';
import TimelineSeparator from '@mui/lab/TimelineSeparator';
import Alert from '@mui/material/Alert';
import Avatar from '@mui/material/Avatar';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import CircularProgress from '@mui/material/CircularProgress';
import Container from '@mui/material/Container';
import Divider from '@mui/material/Divider';
import FormControl from '@mui/material/FormControl';
import Grid from '@mui/material/Grid';
import Link from '@mui/material/Link';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import {FormContainer, TextFieldElement} from 'react-hook-form-mui';
import {DatePickerElement} from 'react-hook-form-mui/date-pickers';

import dayjs, {Dayjs} from 'dayjs';
import IsSameOrBefore from 'dayjs/plugin/isSameOrBefore';
import RelativeTime from 'dayjs/plugin/relativeTime';

import {
  AppRequestByIdPutError,
  AppRequestByIdPutVariables,
  useAppRequestById,
  useAppRequestByIdPut,
} from '../../api/apiComponents';
import {AppRequestDetail, ResolveAppRequestBody} from '../../api/apiSchemas';
import {useCurrentUser} from '../../authentication';
import {canApproveAppRequest} from '../../authorization';
import {timeLimitLabel, useConstraintsForTags} from '../../constraints';
import ConstraintsUnavailableAlert from '../../components/ConstraintsUnavailableAlert';
import Loading from '../../components/Loading';
import RequesterLink, {requesterName} from '../../components/RequesterLink';
import {PrincipalChips, pickByIds} from '../../components/Principals';
import {AccessEndingField, untilIdFor, useTagsByIds} from '../group_requests/Read';
import ServicePrincipalOwners from '../../components/ServicePrincipalOwners';
import ChangeTitle from '../../tab-title';
import NotFound from '../NotFound';

dayjs.extend(RelativeTime);
dayjs.extend(IsSameOrBefore);

function endingLabel(endingAt: string | null | undefined): string {
  return endingAt ? dayjs(endingAt).startOf('second').fromNow() : 'Indefinite';
}

// A labelled set of principals the request names, by id.
interface NamedPrincipals {
  label: string;
  userIds: string[];
  roleIds: string[];
}

// The ids absent from `named`, which lists only the principals still active.
function departedIds(ids: string[], named: {id: string}[] | undefined): string[] {
  const active = new Set((named ?? []).map((principal) => principal.id));
  return ids.filter((id) => !active.has(id));
}

function countLabel(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`;
}

function PrincipalsLine({label, userIds, roleIds, appRequest}: NamedPrincipals & {appRequest: AppRequestDetail}) {
  const users = pickByIds(userIds, appRequest.named_users);
  const roles = pickByIds(roleIds, appRequest.named_roles);
  const departedUsers = departedIds(userIds, appRequest.named_users).length;
  const deletedRoles = departedIds(roleIds, appRequest.named_roles).length;
  const departed = [
    departedUsers > 0 ? countLabel(departedUsers, 'departed user') : null,
    deletedRoles > 0 ? countLabel(deletedRoles, 'deleted role') : null,
  ].filter((part) => part != null);
  return (
    <Stack direction="row" spacing={1} sx={{flexWrap: 'wrap', alignItems: 'center'}}>
      <Typography variant="body2">
        <b>{label}:</b>
      </Typography>
      {users.length + roles.length > 0 && <PrincipalChips users={users} roles={roles} />}
      {departed.length > 0 && (
        <Typography variant="body2" sx={{color: 'text.secondary'}}>
          {departed.join(', ')}
        </Typography>
      )}
      {users.length + roles.length + departed.length === 0 && <Typography variant="body2">None</Typography>}
    </Stack>
  );
}

interface ResolveRequestForm {
  resolved_ownership_ending_at?: string;
  resolved_ownership_ending_at_custom?: string;
  resolved_membership_ending_at?: string;
  resolved_membership_ending_at_custom?: string;
  reason?: string;
}

// The end date an approver's choice sends, matching the group request review page:
// Indefinite (or no choice) sends nothing, which keeps the requested end date.
function approvalEndingAt(until: string | undefined, custom: string | undefined): string | undefined {
  switch (until) {
    case undefined:
    case 'indefinite':
      return undefined;
    case 'custom':
      return custom ? dayjs(custom).toISOString() : undefined;
    default:
      return dayjs().add(parseInt(until, 10), 'seconds').toISOString();
  }
}

export default function ReadAppRequest() {
  const navigate = useNavigate();
  const {id} = useParams();

  const currentUser = useCurrentUser();

  const [requestError, setRequestError] = React.useState('');
  const [approved, setApproved] = React.useState<boolean | null>(null);
  const [submitting, setSubmitting] = React.useState(false);
  const [ownershipUntil, setOwnershipUntil] = React.useState<string | null>(null);
  const [membershipUntil, setMembershipUntil] = React.useState<string | null>(null);

  const {data, isError, isLoading} = useAppRequestById({
    pathParams: {appRequestId: id ?? ''},
  });

  const appRequest: AppRequestDetail = data ?? ({} as AppRequestDetail);

  const ownRequest = appRequest.requester != null && appRequest.requester.id == currentUser.id;
  const canApprove = data != null && canApproveAppRequest(currentUser, appRequest);
  const canResolve = canApprove || ownRequest;

  const requestedTags = useTagsByIds(appRequest.requested_app_tags ?? []).tags;
  const tagConstraints = useConstraintsForTags(appRequest.requested_app_tags ?? []);

  const appGroups = appRequest.requested_app_groups ?? [];
  const namesMembers = appGroups.some(
    (group) => (group.member_user_ids ?? []).length > 0 || (group.member_role_ids ?? []).length > 0,
  );

  const ownershipTimeLimit = tagConstraints.timeLimit(true);
  const membershipTimeLimit = tagConstraints.timeLimit(false);

  const appOwners: NamedPrincipals = {
    label: 'App owners',
    userIds: appRequest.requested_owner_user_ids ?? [],
    roleIds: appRequest.requested_owner_role_ids ?? [],
  };
  const groupPrincipals = appGroups.map((group) => ({
    group,
    owners: {label: 'Owners', userIds: group.owner_user_ids ?? [], roleIds: group.owner_role_ids ?? []},
    members: {label: 'Members', userIds: group.member_user_ids ?? [], roleIds: group.member_role_ids ?? []},
  }));
  // Approval refuses a request naming anyone no longer active, so say where they are named.
  const departedPlaces = [
    {where: 'app owners', principals: appOwners},
    ...groupPrincipals.flatMap(({group, owners, members}) => [
      {where: `${group.name} owners`, principals: owners},
      {where: `${group.name} members`, principals: members},
    ]),
  ].flatMap(({where, principals}) => {
    const count =
      departedIds(principals.userIds, appRequest.named_users).length +
      departedIds(principals.roleIds, appRequest.named_roles).length;
    return count > 0 ? [{where, count}] : [];
  });
  const departedCount = departedPlaces.reduce((total, place) => total + place.count, 0);

  const complete = (
    completedRequest: AppRequestDetail | undefined,
    error: AppRequestByIdPutError | null,
    variables: AppRequestByIdPutVariables,
    context: any,
  ) => {
    setSubmitting(false);
    if (error != null) {
      setRequestError(error.payload.toString());
    } else {
      navigate(0);
    }
  };

  const putResolveRequest = useAppRequestByIdPut({
    onSettled: complete,
  });

  if (isError) {
    return <NotFound />;
  }

  if (isLoading) {
    return <Loading />;
  }

  const defaultFormValues: ResolveRequestForm = {
    resolved_ownership_ending_at: untilIdFor(appRequest.requested_ownership_ending_at, appRequest.created_at),
    resolved_ownership_ending_at_custom: appRequest.requested_ownership_ending_at ?? undefined,
    resolved_membership_ending_at: untilIdFor(appRequest.requested_membership_ending_at, appRequest.created_at),
    resolved_membership_ending_at_custom: appRequest.requested_membership_ending_at ?? undefined,
    reason: '',
  };

  const submit = (responseForm: ResolveRequestForm) => {
    setSubmitting(true);

    const resolveRequest: ResolveAppRequestBody = {
      approved: approved ?? false,
      reason: responseForm.reason ?? '',
    };
    if (canApprove) {
      resolveRequest.resolved_ownership_ending_at = approvalEndingAt(
        responseForm.resolved_ownership_ending_at,
        responseForm.resolved_ownership_ending_at_custom,
      );
      if (namesMembers) {
        resolveRequest.resolved_membership_ending_at = approvalEndingAt(
          responseForm.resolved_membership_ending_at,
          responseForm.resolved_membership_ending_at_custom,
        );
      }
    }

    putResolveRequest.mutate({
      body: resolveRequest,
      pathParams: {appRequestId: appRequest.id},
    });
  };

  const requesterDisplayName = requesterName(appRequest.requester, appRequest.requester_service_principal);
  const appName = appRequest.requested_app_name ?? 'Unknown App';
  const appLink = (
    <Link
      to={`/apps/${appName}`}
      sx={{textDecoration: 'none', color: 'inherit', fontWeight: 500}}
      component={RouterLink}>
      {appName}
    </Link>
  );

  return (
    <React.Fragment>
      <ChangeTitle title={`App Request: ${requesterDisplayName} - ${appName}`} />
      <Container maxWidth="lg" sx={{mt: 4, mb: 4}}>
        <Grid container spacing={3}>
          <Grid
            size={{
              xs: 12,
              md: 5,
              lg: 3,
            }}>
            <Paper
              sx={{
                p: 2,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                height: 240,
              }}>
              <Avatar
                alt={appRequest.status}
                sx={{
                  bgcolor:
                    appRequest.status == 'PENDING'
                      ? 'primary.main'
                      : appRequest.status == 'APPROVED'
                        ? 'success.main'
                        : 'error.main',
                  width: 220,
                  height: 220,
                }}
                variant={'rounded' as any}>
                {appRequest.status == 'PENDING' ? (
                  <PendingIcon sx={{width: 220, height: 220}} />
                ) : appRequest.status == 'APPROVED' ? (
                  <ApprovedIcon sx={{width: 220, height: 220}} />
                ) : (
                  <RejectedIcon sx={{width: 220, height: 220}} />
                )}
              </Avatar>
            </Paper>
          </Grid>
          <Grid
            size={{
              xs: 12,
              md: 7,
              lg: 9,
            }}>
            <Paper
              sx={{
                p: 2,
                height: 240,
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'center',
              }}>
              <Grid container>
                <Grid sx={{textAlign: 'center', wordBreak: 'break-word'}} size={8}>
                  <Typography variant="h4">
                    <RequesterLink
                      user={appRequest.requester}
                      servicePrincipal={appRequest.requester_service_principal}
                      fontWeight={500}
                    />
                  </Typography>
                  {appRequest.requester_service_principal != null && (
                    <Stack direction="row" sx={{justifyContent: 'center', alignItems: 'center', gap: 1, my: 1}}>
                      <Typography variant="body2" color="text.secondary">
                        Owned by
                      </Typography>
                      <ServicePrincipalOwners owners={appRequest.requester_service_principal.owners} />
                    </Stack>
                  )}
                  <Typography variant="h5">
                    {appRequest.status == 'PENDING' ? 'is requesting' : 'requested'} the creation of app
                  </Typography>
                  <Typography variant="h4" sx={{fontWeight: 500}}>
                    {appRequest.status == 'APPROVED' ? appLink : appName}
                  </Typography>
                </Grid>
                <Divider orientation="vertical" flexItem sx={{mr: '-1px'}} />
                <Grid
                  sx={{display: 'flex', flexDirection: 'column', justifyContent: 'center', textAlign: 'center'}}
                  size={4}>
                  <Typography variant="h6">Status</Typography>
                  <Typography variant="h4">
                    <b>{appRequest.status}</b>
                  </Typography>
                </Grid>
              </Grid>
            </Paper>
          </Grid>
          <Grid size={12}>
            <Timeline sx={{[`& .${timelineOppositeContentClasses.root}`]: {flex: 0.1}}}>
              <TimelineItem>
                <TimelineOppositeContent sx={{m: 'auto 0'}} align="right">
                  <span title={appRequest.created_at ?? undefined}>
                    {dayjs(appRequest.created_at).startOf('second').fromNow()}
                  </span>
                </TimelineOppositeContent>
                <TimelineSeparator>
                  <TimelineConnector />
                  <TimelineDot color="primary">
                    <AppRequestIcon />
                  </TimelineDot>
                  <TimelineConnector />
                </TimelineSeparator>
                <TimelineContent>
                  <Paper sx={{p: 2, my: 1}}>
                    <Typography variant="body1">
                      <b>
                        <RequesterLink
                          user={appRequest.requester}
                          servicePrincipal={appRequest.requester_service_principal}
                          showEmail
                        />
                      </b>
                      {appRequest.status == 'PENDING' ? ' is requesting ' : ' requested '}
                      {'the creation of app '}
                      <b>{appRequest.status == 'APPROVED' ? appLink : appName}</b>
                    </Typography>
                    <Typography variant="body2" sx={{pt: 1}}>
                      <b>Reason:</b> {appRequest.request_reason ? appRequest.request_reason : 'No reason given'}
                    </Typography>
                  </Paper>
                  <Paper sx={{p: 2, mt: 1, mb: 0}}>
                    <Typography variant="h6" gutterBottom>
                      Requested Details
                    </Typography>
                    <Grid container spacing={1}>
                      <Grid size={12}>
                        <Typography variant="body2">
                          <b>Name:</b> {appName}
                        </Typography>
                      </Grid>
                      <Grid size={12}>
                        <Typography variant="body2">
                          <b>Description:</b> {appRequest.requested_app_description || 'None'}
                        </Typography>
                      </Grid>
                      <Grid size={12}>
                        <PrincipalsLine {...appOwners} appRequest={appRequest} />
                      </Grid>
                      {requestedTags.length > 0 && (
                        <Grid size={12}>
                          <Stack direction="row" spacing={1} sx={{flexWrap: 'wrap', alignItems: 'center'}}>
                            <Typography variant="body2">
                              <b>Tags:</b>
                            </Typography>
                            {requestedTags.map((tag) => (
                              <Chip key={tag.id} label={tag.name} size="small" />
                            ))}
                          </Stack>
                        </Grid>
                      )}
                      <Grid size={{xs: 12, sm: 6}}>
                        <Typography variant="body2">
                          <b>Ownership Ending:</b> {endingLabel(appRequest.requested_ownership_ending_at)}
                        </Typography>
                      </Grid>
                      {namesMembers && (
                        <Grid size={{xs: 12, sm: 6}}>
                          <Typography variant="body2">
                            <b>Membership Ending:</b> {endingLabel(appRequest.requested_membership_ending_at)}
                          </Typography>
                        </Grid>
                      )}
                      <Grid size={12}>
                        <Typography variant="body2" sx={{pt: 1}}>
                          <b>Initial app groups:</b> {appGroups.length === 0 ? 'None' : null}
                        </Typography>
                      </Grid>
                      {groupPrincipals.map(({group, owners, members}) => (
                        <Grid key={group.name} size={12}>
                          <Paper variant="outlined" sx={{p: 1.5}}>
                            <Typography variant="body1" sx={{fontWeight: 500}}>
                              {group.name}
                            </Typography>
                            {group.description ? (
                              <Typography variant="body2" sx={{color: 'text.secondary', pb: 0.5}}>
                                {group.description}
                              </Typography>
                            ) : null}
                            <PrincipalsLine {...owners} appRequest={appRequest} />
                            <Box sx={{pt: 0.5}}>
                              <PrincipalsLine {...members} appRequest={appRequest} />
                            </Box>
                          </Paper>
                        </Grid>
                      ))}
                    </Grid>
                  </Paper>
                </TimelineContent>
              </TimelineItem>
              <TimelineItem>
                <TimelineOppositeContent sx={{m: 'auto 0'}} align="right">
                  {appRequest.resolved_at != null ? (
                    <span title={appRequest.resolved_at}>
                      {dayjs(appRequest.resolved_at).startOf('second').fromNow()}
                    </span>
                  ) : null}
                </TimelineOppositeContent>
                <TimelineSeparator>
                  <TimelineConnector />
                  {appRequest.status == 'PENDING' ? (
                    <TimelineDot color="primary">
                      <PendingIcon />
                    </TimelineDot>
                  ) : appRequest.status == 'APPROVED' ? (
                    <TimelineDot color="success">
                      <ApprovedIcon />
                    </TimelineDot>
                  ) : (
                    <TimelineDot color="error">
                      <RejectedIcon />
                    </TimelineDot>
                  )}
                  <TimelineConnector />
                </TimelineSeparator>
                <TimelineContent>
                  {appRequest.status == 'PENDING' ? (
                    canResolve ? (
                      <Box sx={{my: 2}}>
                        <Paper sx={{p: 2}}>
                          <Typography variant="body1">
                            {ownRequest ? (
                              <>
                                Request is <b>pending</b>. You may withdraw your request by rejecting it below.
                              </>
                            ) : (
                              <>
                                Request is <b>pending</b>. Review the requested app above, optionally change the end
                                dates, then approve or reject.
                              </>
                            )}
                          </Typography>
                        </Paper>
                        <Paper sx={{p: 2, mt: 1}}>
                          <FormContainer<ResolveRequestForm>
                            defaultValues={defaultFormValues}
                            onSuccess={(formData) => submit(formData)}>
                            {requestError != '' ? (
                              <Alert severity="error" sx={{my: 1}}>
                                {requestError}
                              </Alert>
                            ) : null}
                            {canApprove && departedCount > 0 && (
                              <Alert severity="warning" sx={{my: 1}}>
                                {`This request names ${departedCount} ${departedCount === 1 ? 'user or role' : 'users or roles'} no longer active (` +
                                  departedPlaces.map((place) => `${place.count} in ${place.where}`).join('; ') +
                                  "), so it can't be approved as filed."}
                              </Alert>
                            )}
                            {canApprove && (
                              <>
                                <ConstraintsUnavailableAlert constraints={tagConstraints} action="approval" />
                                <Grid container columnSpacing={2} rowSpacing={0} sx={{alignItems: 'flex-start'}}>
                                  {ownershipTimeLimit != null && (
                                    <Grid size={12}>
                                      <Typography variant="subtitle2" sx={{color: 'text.accent', pt: 1}}>
                                        {'Ownership is limited to ' +
                                          timeLimitLabel(ownershipTimeLimit) +
                                          ' by a tag constraint.'}
                                      </Typography>
                                    </Grid>
                                  )}
                                  <Grid size={6}>
                                    <AccessEndingField
                                      name="resolved_ownership_ending_at"
                                      label="Ownership Ending At"
                                      timeLimit={ownershipTimeLimit}
                                      constraintsBlocked={tagConstraints.blocked}
                                      setUntil={setOwnershipUntil}
                                    />
                                  </Grid>
                                  <Grid size={6}>
                                    {ownershipUntil == 'custom' ? (
                                      <FormControl margin="normal" fullWidth>
                                        <DatePickerElement
                                          label="Custom End Date"
                                          name="resolved_ownership_ending_at_custom"
                                          shouldDisableDate={(date: Dayjs) => date.isSameOrBefore(dayjs(), 'day')}
                                          maxDate={
                                            ownershipTimeLimit ? dayjs().add(ownershipTimeLimit, 'second') : undefined
                                          }
                                        />
                                      </FormControl>
                                    ) : null}
                                  </Grid>
                                </Grid>
                                {namesMembers && (
                                  <Grid container columnSpacing={2} rowSpacing={0} sx={{alignItems: 'flex-start'}}>
                                    {membershipTimeLimit != null && (
                                      <Grid size={12}>
                                        <Typography variant="subtitle2" sx={{color: 'text.accent', pt: 1}}>
                                          {'Membership is limited to ' +
                                            timeLimitLabel(membershipTimeLimit) +
                                            ' by a tag constraint.'}
                                        </Typography>
                                      </Grid>
                                    )}
                                    <Grid size={6}>
                                      <AccessEndingField
                                        name="resolved_membership_ending_at"
                                        label="Membership Ending At"
                                        timeLimit={membershipTimeLimit}
                                        constraintsBlocked={tagConstraints.blocked}
                                        setUntil={setMembershipUntil}
                                      />
                                    </Grid>
                                    <Grid size={6}>
                                      {membershipUntil == 'custom' ? (
                                        <FormControl margin="normal" fullWidth>
                                          <DatePickerElement
                                            label="Custom Membership End Date"
                                            name="resolved_membership_ending_at_custom"
                                            shouldDisableDate={(date: Dayjs) => date.isSameOrBefore(dayjs(), 'day')}
                                            maxDate={
                                              membershipTimeLimit
                                                ? dayjs().add(membershipTimeLimit, 'second')
                                                : undefined
                                            }
                                          />
                                        </FormControl>
                                      ) : null}
                                    </Grid>
                                  </Grid>
                                )}
                                <Divider sx={{my: 2}} />
                              </>
                            )}
                            <FormControl margin="normal" fullWidth>
                              <TextFieldElement
                                label={
                                  ownRequest
                                    ? 'Why? (provide a reason)'
                                    : 'Why is this app creation approved or rejected? (provide a reason)'
                                }
                                name="reason"
                                multiline
                                rows={4}
                                rules={{maxLength: 1024}}
                                parseError={(error) => {
                                  if (error?.message != '') return error?.message ?? '';
                                  if (error.type == 'maxLength')
                                    return 'Reason can be at most 1024 characters in length';
                                  return '';
                                }}
                              />
                            </FormControl>
                            <FormControl margin="normal" style={{flexDirection: 'row'}}>
                              {canApprove && (
                                <Button
                                  variant="contained"
                                  color="success"
                                  size="large"
                                  type="submit"
                                  startIcon={<ApprovedIcon />}
                                  sx={{mx: 2}}
                                  disabled={submitting || tagConstraints.blocked || departedCount > 0}
                                  onClick={() => setApproved(true)}>
                                  Approve
                                </Button>
                              )}
                              <Button
                                variant="contained"
                                color="error"
                                size="large"
                                type="submit"
                                startIcon={<RejectedIcon />}
                                sx={{mx: 2}}
                                disabled={submitting}
                                onClick={() => setApproved(false)}>
                                {ownRequest ? 'Withdraw' : 'Reject'}
                              </Button>
                              {submitting ? <CircularProgress sx={{mx: 2}} size={40} /> : null}
                            </FormControl>
                          </FormContainer>
                        </Paper>
                      </Box>
                    ) : (
                      <Paper sx={{p: 2, my: 2}}>
                        <Typography variant="body1">
                          Request is <b>pending</b> and can be reviewed by Access admins.
                        </Typography>
                      </Paper>
                    )
                  ) : (
                    <Paper sx={{p: 2, my: 2}}>
                      <Typography variant="body1">
                        {appRequest.resolver_service_principal != null ? (
                          <b>
                            <RequesterLink servicePrincipal={appRequest.resolver_service_principal} />
                          </b>
                        ) : appRequest.resolver == null ? (
                          <b>Access</b>
                        ) : (
                          <Link
                            to={`/users/${appRequest.resolver?.email.toLowerCase()}`}
                            sx={{textDecoration: 'none', color: 'inherit'}}
                            component={RouterLink}>
                            <b>{appRequest.resolver?.email.toLowerCase()}</b>
                          </Link>
                        )}
                        {appRequest.status == 'APPROVED' ? (
                          <>
                            {' approved the request and created app '}
                            <b>{appLink}</b>
                            {'.'}
                          </>
                        ) : (
                          ' rejected the request.'
                        )}
                      </Typography>
                      <Typography variant="body2" sx={{py: 1}}>
                        <b>Reason:</b> {appRequest.resolution_reason ? appRequest.resolution_reason : 'No reason given'}
                      </Typography>
                      {appRequest.status == 'APPROVED' && (
                        <>
                          <Typography variant="body2">
                            <b>Ownership Ending:</b> {endingLabel(appRequest.resolved_ownership_ending_at)}
                          </Typography>
                          {namesMembers && (
                            <Typography variant="body2">
                              <b>Membership Ending:</b> {endingLabel(appRequest.resolved_membership_ending_at)}
                            </Typography>
                          )}
                        </>
                      )}
                    </Paper>
                  )}
                </TimelineContent>
              </TimelineItem>
            </Timeline>
          </Grid>
        </Grid>
      </Container>
    </React.Fragment>
  );
}
