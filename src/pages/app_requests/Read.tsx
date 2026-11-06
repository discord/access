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
import {AppRequestDetail, OktaUserSummary, ResolveAppRequestBody} from '../../api/apiSchemas';
import {useCurrentUser} from '../../authentication';
import {canApproveAppRequest} from '../../authorization';
import {timeLimitLabel, useConstraintsForTags} from '../../constraints';
import ConstraintsUnavailableAlert from '../../components/ConstraintsUnavailableAlert';
import Loading from '../../components/Loading';
import RequesterLink, {requesterName} from '../../components/RequesterLink';
import {PrincipalChips, RoleOption, pickByIds} from '../../components/Principals';
import {AccessEndingField, untilIdFor} from '../../components/AccessLength';
import {useTagsByIds} from '../../components/useTagsByIds';
import ServicePrincipalOwners from '../../components/ServicePrincipalOwners';
import ChangeTitle from '../../tab-title';
import NotFound from '../NotFound';

dayjs.extend(RelativeTime);
dayjs.extend(IsSameOrBefore);

// The seconds from `askedAt` to `endingAt`, rounded as `untilIdFor` rounds, or null for no end.
function requestedSeconds(endingAt: string | null | undefined, askedAt: string | null | undefined): number | null {
  if (!endingAt) {
    return null;
  }
  return Math.round(dayjs(endingAt).diff(dayjs(askedAt), 'second') / 100) * 100;
}

function minLimit(a: number | null, b: number | null): number | null {
  if (a == null) return b;
  if (b == null) return a;
  return Math.min(a, b);
}

// The end date an approver's selection sets, or undefined to keep the requested one.
function resolvedEndingAt(until: string | undefined, custom: string | undefined): string | undefined {
  switch (until) {
    case 'indefinite':
    case undefined:
      return undefined;
    case 'custom':
      return custom ? dayjs(custom).toISOString() : undefined;
    default:
      return dayjs().add(parseInt(until, 10), 'seconds').toISOString();
  }
}

function endingLabel(endingAt: string | null | undefined): string {
  return endingAt ? dayjs(endingAt).startOf('second').fromNow() : 'Indefinite';
}

function PrincipalsLine({label, users, roles}: {label: string; users: OktaUserSummary[]; roles: RoleOption[]}) {
  return (
    <Stack direction="row" spacing={1} sx={{flexWrap: 'wrap', alignItems: 'center'}}>
      <Typography variant="body2">
        <b>{label}:</b>
      </Typography>
      {users.length + roles.length > 0 ? (
        <PrincipalChips users={users} roles={roles} />
      ) : (
        <Typography variant="body2">None</Typography>
      )}
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

  // The approver may shorten the requested end dates, not lift them: the API keeps the
  // requested date when none is sent, so Indefinite and longer durations are not offered.
  const ownershipTimeLimit = minLimit(
    tagConstraints.timeLimit(true),
    requestedSeconds(appRequest.requested_ownership_ending_at, appRequest.created_at),
  );
  const membershipTimeLimit = minLimit(
    tagConstraints.timeLimit(false),
    requestedSeconds(appRequest.requested_membership_ending_at, appRequest.created_at),
  );

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
      resolveRequest.resolved_ownership_ending_at = resolvedEndingAt(
        responseForm.resolved_ownership_ending_at,
        responseForm.resolved_ownership_ending_at_custom,
      );
      if (namesMembers) {
        resolveRequest.resolved_membership_ending_at = resolvedEndingAt(
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
  const ownerUsers = pickByIds(appRequest.requested_owner_user_ids, appRequest.named_users);
  const ownerRoles = pickByIds(appRequest.requested_owner_role_ids, appRequest.named_roles);

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
                        {ownerUsers.length + ownerRoles.length > 0 ? (
                          <PrincipalsLine label="App owners" users={ownerUsers} roles={ownerRoles} />
                        ) : (
                          <Typography variant="body2">
                            <b>App owners:</b> The requester
                          </Typography>
                        )}
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
                      {appGroups.map((group) => (
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
                            <PrincipalsLine
                              label="Owners"
                              users={pickByIds(group.owner_user_ids, appRequest.named_users)}
                              roles={pickByIds(group.owner_role_ids, appRequest.named_roles)}
                            />
                            <Box sx={{pt: 0.5}}>
                              <PrincipalsLine
                                label="Members"
                                users={pickByIds(group.member_user_ids, appRequest.named_users)}
                                roles={pickByIds(group.member_role_ids, appRequest.named_roles)}
                              />
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
                                Request is <b>pending</b>. Review the requested app above, optionally shorten the end
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
                            {canApprove && (
                              <>
                                <ConstraintsUnavailableAlert constraints={tagConstraints} action="approval" />
                                <Grid container columnSpacing={2} rowSpacing={0} sx={{alignItems: 'flex-start'}}>
                                  {tagConstraints.timeLimit(true) != null && (
                                    <Grid size={12}>
                                      <Typography variant="subtitle2" sx={{color: 'text.accent', pt: 1}}>
                                        {'Ownership is limited to ' +
                                          timeLimitLabel(tagConstraints.timeLimit(true)!) +
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
                                    {tagConstraints.timeLimit(false) != null && (
                                      <Grid size={12}>
                                        <Typography variant="subtitle2" sx={{color: 'text.accent', pt: 1}}>
                                          {'Membership is limited to ' +
                                            timeLimitLabel(tagConstraints.timeLimit(false)!) +
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
                                  disabled={submitting || tagConstraints.blocked}
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
                        {appRequest.resolver == null ? (
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
