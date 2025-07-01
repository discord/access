import React from 'react';
import {Link as RouterLink, useParams, useNavigate} from 'react-router-dom';

import Link from '@mui/material/Link';
import Container from '@mui/material/Container';
import Grid from '@mui/material/Grid';
import Box from '@mui/material/Box';
import Paper from '@mui/material/Paper';
import Button from '@mui/material/Button';
import Avatar from '@mui/material/Avatar';
import Typography from '@mui/material/Typography';
import Divider from '@mui/material/Divider';
import AccessRequestIcon from '../../components/icons/MoreTime';
import PendingIcon from '@mui/icons-material/HelpOutline';
import ApprovedIcon from '@mui/icons-material/CheckCircleOutline';
import RejectedIcon from '@mui/icons-material/HighlightOff';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Alert from '@mui/material/Alert';
import FormControl from '@mui/material/FormControl';
import Timeline from '@mui/lab/Timeline';
import TimelineItem from '@mui/lab/TimelineItem';
import TimelineSeparator from '@mui/lab/TimelineSeparator';
import TimelineConnector from '@mui/lab/TimelineConnector';
import TimelineContent from '@mui/lab/TimelineContent';
import CircularProgress from '@mui/material/CircularProgress';
import TimelineOppositeContent, {timelineOppositeContentClasses} from '@mui/lab/TimelineOppositeContent';
import TimelineDot from '@mui/lab/TimelineDot';
import Chip from '@mui/material/Chip';
import {
  FormContainer,
  SelectElement,
  DatePickerElement,
  TextFieldElement,
  ToggleButtonGroupElement,
} from 'react-hook-form-mui';

import dayjs, {Dayjs} from 'dayjs';
import RelativeTime from 'dayjs/plugin/relativeTime';
import IsSameOrBefore from 'dayjs/plugin/isSameOrBefore';

import {
  groupBy,
  displayUserName,
  minTagTime,
  minTagTimeGroups,
  requiredReason,
  requiredReasonGroups,
} from '../../helpers';
import {useCurrentUser} from '../../authentication';
import {canManageGroup, ACCESS_APP_RESERVED_NAME} from '../../authorization';
import {
  useGetRequestById,
  useGetGroupById,
  useGetAppById,
  useResolveRequestById,
  useGetUserGroupAudits,
  useGetGroupRoleAudits,
  ResolveRequestByIdError,
  ResolveRequestByIdVariables,
} from '../../api/apiComponents';
import {
  AccessRequest,
  ResolveAccessRequest,
  PolymorphicGroup,
  OktaUserGroupMember,
  AppGroup,
  App,
  OktaGroup,
  RoleGroup,
} from '../../api/apiSchemas';

import NotFound from '../NotFound';
import ChangeTitle from '../../tab-title';
import Loading from '../../components/Loading';
import accessConfig from '../../config/accessConfig';
import {EmptyListEntry} from '../../components/EmptyListEntry';
import AccessHistory from '../../components/AccessHistory';

dayjs.extend(RelativeTime);
dayjs.extend(IsSameOrBefore);

function sortGroupMembers(
  [aUserId, aUsers]: [string, Array<OktaUserGroupMember>],
  [bUserId, bUsers]: [string, Array<OktaUserGroupMember>],
): number {
  let aEmail = aUsers[0].active_user?.email ?? '';
  let bEmail = bUsers[0].active_user?.email ?? '';
  return aEmail.localeCompare(bEmail);
}

interface ResolveRequestForm {
  until?: string;
  customUntil?: string;
  reason?: string;
}

const GROUP_TYPE_ID_TO_LABELS: Record<string, string> = {
  okta_group: 'Group',
  app_group: 'App Group',
  role_group: 'Role',
} as const;

const RFC822_FORMAT = 'ddd, DD MMM YYYY HH:mm:ss ZZ';

const UNTIL_ID_TO_LABELS: Record<string, string> = accessConfig.ACCESS_TIME_LABELS;
const UNTIL_JUST_NUMERIC_ID_TO_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries(UNTIL_ID_TO_LABELS).filter(([key]) => !isNaN(Number(key))),
);
const UNTIL_OPTIONS = Object.entries(UNTIL_ID_TO_LABELS).map(([id, label], index) => ({id: id, label: label}));

function ComputeConstraints(accessRequest: AccessRequest) {
  const group = accessRequest.requested_group ?? null;

  if (group == null) {
    return [null, null];
  }

  let timeLimit = minTagTime(
    group.active_group_tags ? group.active_group_tags.map((tagMap) => tagMap.active_tag!) : [],
    accessRequest.request_ownership!,
  );

  let reason = requiredReason(
    group.active_group_tags ? group.active_group_tags?.map((tagMap) => tagMap.active_tag!) : [],
    accessRequest.request_ownership!,
  );

  if (group.type == 'role_group' && !accessRequest.request_ownership) {
    const active_groups_owners = (group as RoleGroup).active_role_associated_group_owner_mappings?.reduce(
      (out, curr) => {
        curr.active_group ? out.push(curr.active_group) : null;
        return out;
      },
      new Array<OktaGroup | AppGroup>(),
    );
    const active_groups_members = (group as RoleGroup).active_role_associated_group_member_mappings?.reduce(
      (out, curr) => {
        curr.active_group ? out.push(curr.active_group) : null;
        return out;
      },
      new Array<OktaGroup | AppGroup>(),
    );

    reason =
      reason ||
      requiredReasonGroups(active_groups_members ?? [], false) ||
      requiredReasonGroups(active_groups_owners ?? [], true);
  }

  return [timeLimit, reason];
}

export default function ReadRequest() {
  const navigate = useNavigate();
  const {id} = useParams();

  const currentUser = useCurrentUser();

  const [until, setUntil] = React.useState<string | null>(null);
  const [requestError, setRequestError] = React.useState('');
  const [approved, setApproved] = React.useState<boolean | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  const {data, isError, isLoading} = useGetRequestById({
    pathParams: {accessRequestId: id ?? ''},
  });

  const accessRequest = data ?? ({} as AccessRequest);

  const ownRequest = accessRequest.requester?.id == currentUser.id;

  const requestEndingAt = dayjs(accessRequest.request_ending_at);
  // round the delta to adjust based on partial seconds
  const requestedUntilDelta =
    accessRequest.request_ending_at == null
      ? null
      : Math.round(requestEndingAt.diff(dayjs(accessRequest.created_at), 'second') / 100) * 100;
  const requestedUntil =
    requestedUntilDelta == null
      ? 'indefinite'
      : requestedUntilDelta in UNTIL_ID_TO_LABELS
        ? requestedUntilDelta.toString()
        : 'custom';

  const requestedGroupManager = canManageGroup(currentUser, accessRequest.requested_group);

  const complete = (
    completedAccessRequest: ResolveAccessRequest | undefined,
    error: ResolveRequestByIdError | null,
    variables: ResolveRequestByIdVariables,
    context: any,
  ) => {
    setSubmitting(false);
    if (error != null) {
      setRequestError(error.payload.toString());
    } else {
      navigate(0);
    }
  };

  const putResolveRequest = useResolveRequestById({
    onSettled: complete,
  });

  const {data: groupData} = useGetGroupById(
    {
      pathParams: {groupId: accessRequest.requested_group?.id ?? ''},
    },
    {
      enabled: accessRequest.requested_group != null && (!requestedGroupManager || ownRequest),
    },
  );

  const group = groupData ?? ({} as PolymorphicGroup);

  const constraints = ComputeConstraints(accessRequest);

  const timeLimit: number | null = constraints[0] as number | null;
  const reason: boolean = constraints[1] as boolean;

  let autofill_until = false;
  if (requestedUntilDelta && timeLimit && requestedUntilDelta <= timeLimit) {
    autofill_until = true;
  }

  let labels = null;
  let requestedUntilAdjusted = null;
  if (!(timeLimit == null)) {
    const filteredUntil = Object.keys(UNTIL_JUST_NUMERIC_ID_TO_LABELS)
      .filter((key) => Number(key) <= timeLimit!)
      .reduce(
        (obj, key) => {
          obj[key] = UNTIL_JUST_NUMERIC_ID_TO_LABELS[key];
          return obj;
        },
        {} as Record<string, string>,
      );

    requestedUntilAdjusted = Object.keys(filteredUntil).at(-1);

    labels = Object.entries(Object.assign({}, filteredUntil, {custom: 'Custom'})).map(([id, label], index) => ({
      id: id,
      label: label,
    }));
  }

  const ownerships = groupBy(group.active_user_ownerships, (m) => m.active_user?.id);

  const {data: appData} = useGetAppById(
    {
      pathParams: {
        appId: ((accessRequest.requested_group ?? {}) as AppGroup).app?.id ?? '',
      },
    },
    {
      enabled: accessRequest.requested_group?.type == 'app_group' && (!requestedGroupManager || ownRequest),
    },
  );

  const app = appData ?? ({} as App);

  const appOwnershipsArray = (app.active_owner_app_groups ?? [])
    .map((appGroup) => appGroup.active_user_ownerships ?? [])
    .flat();
  const appOwnerships = groupBy(appOwnershipsArray, (m) => m.active_user?.id);

  const {data: accessAppData} = useGetAppById(
    {
      pathParams: {
        appId: ACCESS_APP_RESERVED_NAME,
      },
    },
    {
      enabled:
        accessRequest.requested_group != null &&
        (!requestedGroupManager || ownRequest) &&
        group.active_user_ownerships?.length == 0 &&
        (accessRequest.requested_group?.type != 'app_group' || appOwnershipsArray.length == 0),
    },
  );

  const accessApp = accessAppData ?? ({} as App);

  const accessAppOwnerships = groupBy(
    (accessApp.active_owner_app_groups ?? []).map((appGroup) => appGroup.active_user_memberships ?? []).flat(),
    (m) => m.active_user?.id,
  );

  const {data: userGroupAudits} = useGetUserGroupAudits({
    queryParams: {
      user_id: accessRequest.requester?.id ?? '',
      group_id: accessRequest.requested_group?.id ?? '',
      per_page: 50,
      order_by: 'created_at',
      order_desc: true,
    },
  });

  const {data: groupRoleAudits} = useGetGroupRoleAudits({
    queryParams: {
      group_id: accessRequest.requested_group?.id ?? '',
      per_page: 50,
      order_by: 'created_at',
      order_desc: true,
    },
  });

  if (isError) {
    return <NotFound />;
  }

  if (isLoading) {
    return <Loading />;
  }

  const submit = (responseForm: ResolveRequestForm) => {
    if (reason && approved && responseForm.reason && responseForm.reason.trim() == '') {
      setRequestError('This group requires that the reason field is filled.');
      return;
    }

    setSubmitting(true);

    const resolveRequest = {
      approved: approved ?? false,
      reason: responseForm.reason ?? '',
    } as ResolveAccessRequest;

    switch (responseForm.until) {
      case 'indefinite':
        break;
      case 'custom':
        resolveRequest.ending_at = (responseForm.customUntil as unknown as Dayjs).format(RFC822_FORMAT);
        break;
      default:
        resolveRequest.ending_at = dayjs()
          .add(parseInt(responseForm.until ?? '0', 10), 'seconds')
          .format(RFC822_FORMAT);
        break;
    }

    putResolveRequest.mutate({
      body: resolveRequest,
      pathParams: {accessRequestId: accessRequest.id},
    });
  };

  // Filter audit data for the specific group and user
  const userGroupHistory =
    userGroupAudits?.results?.filter(
      (audit) =>
        audit.group?.id === accessRequest.requested_group?.id && audit.user?.id === accessRequest.requester?.id,
    ) ?? [];

  // Get alternative role mappings for this group
  const alternativeRoleMappings = groupRoleAudits?.results ?? [];

  return (
    <React.Fragment>
      <ChangeTitle
        title={`Request: ${displayUserName(accessRequest.requester)} ${accessRequest.request_ownership ? 'ownership of' : 'membership to'} ${accessRequest.requested_group!.name}`}
      />
      <Container maxWidth="lg" sx={{mt: 4, mb: 4}}>
        <Grid container spacing={3}>
          <Grid item xs={12} md={5} lg={3}>
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
                alt={accessRequest.status}
                sx={{
                  bgcolor:
                    accessRequest.status == 'PENDING'
                      ? 'primary.main'
                      : accessRequest.status == 'APPROVED'
                        ? 'success.main'
                        : 'error.main',
                  width: 220,
                  height: 220,
                }}
                variant={'rounded' as any}>
                {accessRequest.status == 'PENDING' ? (
                  <PendingIcon
                    sx={{
                      width: 220,
                      height: 220,
                    }}
                  />
                ) : accessRequest.status == 'APPROVED' ? (
                  <ApprovedIcon
                    sx={{
                      width: 220,
                      height: 220,
                    }}
                  />
                ) : (
                  <RejectedIcon
                    sx={{
                      width: 220,
                      height: 220,
                    }}
                  />
                )}
              </Avatar>
            </Paper>
          </Grid>
          <Grid item xs={12} md={7} lg={9}>
            <Paper
              sx={{
                p: 2,
                height: 240,
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'center',
                position: 'relative',
              }}>
              <Grid container>
                <Grid
                  item
                  xs={8}
                  sx={{
                    textAlign: 'center',
                    wordBreak: 'break-word',
                  }}>
                  <Typography variant="h4">
                    {(accessRequest.requester?.deleted_at ?? null) != null ? (
                      <Link
                        to={`/users/${accessRequest.requester?.id ?? ''}`}
                        sx={{textDecoration: 'line-through', color: 'inherit', fontWeight: 500}}
                        component={RouterLink}>
                        {displayUserName(accessRequest.requester)}
                      </Link>
                    ) : (
                      <Link
                        to={`/users/${accessRequest.requester?.email.toLowerCase() ?? ''}`}
                        sx={{textDecoration: 'none', color: 'inherit', fontWeight: 500}}
                        component={RouterLink}>
                        {displayUserName(accessRequest.requester)}
                      </Link>
                    )}
                  </Typography>
                  <Typography variant="h5">
                    {accessRequest.status == 'PENDING' ? 'is requesting' : 'requested'}{' '}
                    {accessRequest.request_ownership ? (
                      <>
                        <Box component="span" sx={{color: 'primary.main', fontWeight: 'bold'}}>
                          ownership
                        </Box>{' '}
                        of
                      </>
                    ) : (
                      'membership to'
                    )}
                  </Typography>
                  <Typography variant="h4">
                    {(accessRequest.requested_group?.deleted_at ?? null) != null ? (
                      <Link
                        to={`/groups/${accessRequest.requested_group?.id ?? ''}`}
                        sx={{textDecoration: 'line-through', color: 'inherit', fontWeight: 500}}
                        component={RouterLink}>
                        {accessRequest.requested_group?.name ?? ''}
                      </Link>
                    ) : (
                      <Link
                        to={`/groups/${accessRequest.requested_group?.name ?? ''}`}
                        sx={{textDecoration: 'none', color: 'inherit', fontWeight: 500}}
                        component={RouterLink}>
                        {accessRequest.requested_group?.name ?? ''}
                      </Link>
                    )}
                  </Typography>
                </Grid>
                <Divider orientation="vertical" flexItem sx={{mr: '-1px'}} />
                <Grid
                  item
                  xs={4}
                  sx={{
                    display: 'flex',
                    flexDirection: 'column',
                    justifyContent: 'center',
                    textAlign: 'center',
                  }}>
                  <Typography variant="h6">Status</Typography>
                  <Typography variant="h4">
                    <b>{accessRequest.status}</b>
                  </Typography>
                </Grid>
              </Grid>
            </Paper>
          </Grid>

          {/* Historical Access Information Section */}
          <Grid item xs={12}>
            <AccessHistory
              subjectType="user"
              subjectName={displayUserName(accessRequest.requester)}
              groupName={accessRequest.requested_group?.name ?? ''}
              auditHistory={userGroupHistory}
              alternativeRoleMappings={alternativeRoleMappings}
            />
          </Grid>

          <Grid item xs={12}>
            <Timeline
              sx={{
                [`& .${timelineOppositeContentClasses.root}`]: {
                  flex: 0.1,
                },
              }}>
              <TimelineItem>
                <TimelineOppositeContent sx={{m: 'auto 0'}} align="right">
                  <span title={accessRequest.created_at}>
                    {dayjs(accessRequest.created_at).startOf('second').fromNow()}
                  </span>
                </TimelineOppositeContent>
                <TimelineSeparator>
                  <TimelineConnector />
                  <TimelineDot color="primary">
                    <AccessRequestIcon />
                  </TimelineDot>
                  <TimelineConnector />
                </TimelineSeparator>
                <TimelineContent>
                  <Paper sx={{p: 2, my: 2}}>
                    <Typography variant="body1">
                      {(accessRequest.requester?.deleted_at ?? null) != null ? (
                        <Link
                          to={`/users/${accessRequest.requester?.id}`}
                          sx={{
                            textDecoration: 'line-through',
                            color: 'inherit',
                          }}
                          component={RouterLink}>
                          <b>{accessRequest.requester?.email.toLowerCase()}</b>
                        </Link>
                      ) : (
                        <Link
                          to={`/users/${accessRequest.requester?.email.toLowerCase()}`}
                          sx={{
                            textDecoration: 'none',
                            color: 'inherit',
                          }}
                          component={RouterLink}>
                          <b>{accessRequest.requester?.email.toLowerCase()}</b>
                        </Link>
                      )}
                      {accessRequest.status == 'PENDING' ? ' is requesting ' : ' requested '}
                      {accessRequest.request_ownership ? 'ownership of ' : 'membership to '}
                      {(accessRequest.requested_group?.deleted_at ?? null) != null ? (
                        <Link
                          to={`/groups/${accessRequest.requested_group?.id}`}
                          sx={{
                            textDecoration: 'line-through',
                            color: 'inherit',
                          }}
                          component={RouterLink}>
                          <b>{accessRequest.requested_group?.name}</b>
                        </Link>
                      ) : (
                        <Link
                          to={`/groups/${accessRequest.requested_group?.name}`}
                          sx={{
                            textDecoration: 'none',
                            color: 'inherit',
                          }}
                          component={RouterLink}>
                          <b>{accessRequest.requested_group?.name}</b>
                        </Link>
                      )}{' '}
                      ending{' '}
                      <b>
                        {accessRequest.request_ending_at == null
                          ? 'never'
                          : dayjs(accessRequest.request_ending_at).startOf('second').fromNow()}
                      </b>
                    </Typography>
                    <Typography variant="body2">
                      <b>Reason:</b> {accessRequest.request_reason ? accessRequest.request_reason : 'No reason given'}
                    </Typography>
                  </Paper>
                </TimelineContent>
              </TimelineItem>
              <TimelineItem>
                <TimelineOppositeContent sx={{m: 'auto 0'}} align="right">
                  {accessRequest.resolved_at != null ? (
                    <span title={accessRequest.resolved_at}>
                      {dayjs(accessRequest.resolved_at).startOf('second').fromNow()}
                    </span>
                  ) : null}
                </TimelineOppositeContent>
                <TimelineSeparator>
                  <TimelineConnector />
                  {accessRequest.status == 'PENDING' ? (
                    <TimelineDot color="primary">
                      <PendingIcon />
                    </TimelineDot>
                  ) : accessRequest.status == 'APPROVED' ? (
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
                  {accessRequest.status == 'PENDING' ? (
                    <>
                      {requestedGroupManager || ownRequest ? (
                        <Box sx={{my: 2}}>
                          <Paper sx={{p: 2}}>
                            <Typography variant="body1">
                              Request is <b>pending</b>,
                              {ownRequest ? ' you can reject your own request' : ' please add a review'}
                            </Typography>
                          </Paper>
                          <Paper sx={{p: 2, mt: 1}}>
                            <FormContainer<ResolveRequestForm>
                              defaultValues={
                                timeLimit && !autofill_until && requestedUntilAdjusted
                                  ? {until: requestedUntilAdjusted} // case where time limit lowered below requested time
                                  : {until: requestedUntil, customUntil: (requestEndingAt as unknown as string) ?? ''}
                              }
                              onSuccess={(formData) => submit(formData)}>
                              {requestError != '' ? <Alert severity="error">{requestError}</Alert> : null}
                              {!ownRequest ? (
                                <FormControl fullWidth>
                                  <Grid container>
                                    <Grid item xs={12}>
                                      <Typography variant="subtitle1" color="text.accent">
                                        {timeLimit
                                          ? (accessRequest.request_ownership ? 'Ownership of ' : 'Membership to ') +
                                            'this group is limited to ' +
                                            Math.floor(timeLimit / 86400) +
                                            ' days.'
                                          : null}
                                      </Typography>
                                    </Grid>
                                    <Grid item xs={8}>
                                      <SelectElement
                                        fullWidth
                                        label="For how long?"
                                        name="until"
                                        options={labels ?? UNTIL_OPTIONS}
                                        onChange={(value) => setUntil(value)}
                                        required
                                      />
                                    </Grid>
                                    <Grid item xs={1} />
                                    <Grid item xs={2}>
                                      <ToggleButtonGroupElement
                                        name="ownerOrMember"
                                        enforceAtLeastOneSelected
                                        exclusive
                                        disabled
                                        options={[
                                          {
                                            id: 'owner',
                                            label: 'Owner',
                                            selected: accessRequest.request_ownership,
                                          },
                                          {
                                            id: 'member',
                                            label: 'Member',
                                            selected: !accessRequest.request_ownership,
                                          },
                                        ]}
                                      />
                                    </Grid>
                                    <Grid item xs={1} />
                                  </Grid>
                                </FormControl>
                              ) : null}
                              {!ownRequest && ((until == null && requestedUntil == 'custom') || until == 'custom') ? (
                                <FormControl margin="normal" fullWidth required>
                                  <DatePickerElement
                                    label="Custom End Date"
                                    name="customUntil"
                                    shouldDisableDate={(date: Dayjs) => date.isSameOrBefore(dayjs(), 'day')}
                                    maxDate={timeLimit ? dayjs().add(timeLimit, 'second') : null}
                                    required
                                  />
                                </FormControl>
                              ) : null}
                              <FormControl margin="normal" fullWidth>
                                <TextFieldElement
                                  label="Why? (provide a reason)"
                                  name="reason"
                                  multiline
                                  rows={4}
                                  validation={{maxLength: 1024}}
                                  parseError={(error) => {
                                    if (error?.message != '') {
                                      return error?.message ?? '';
                                    }
                                    if (error.type == 'maxLength') {
                                      return 'Reason can be at most 1024 characters in length';
                                    }
                                    return '';
                                  }}
                                />
                              </FormControl>
                              <FormControl margin="normal" style={{flexDirection: 'row'}}>
                                <Button
                                  variant="contained"
                                  color="success"
                                  size="large"
                                  type="submit"
                                  startIcon={<ApprovedIcon />}
                                  sx={{mx: 2}}
                                  disabled={submitting || ownRequest}
                                  onClick={() => setApproved(true)}>
                                  Approve
                                </Button>
                                <Button
                                  variant="contained"
                                  color="error"
                                  size="large"
                                  type="submit"
                                  startIcon={<RejectedIcon />}
                                  sx={{mx: 2}}
                                  disabled={submitting}
                                  onClick={() => setApproved(false)}>
                                  Reject
                                </Button>
                                {submitting ? <CircularProgress sx={{mx: 2}} size={40} /> : null}
                              </FormControl>
                            </FormContainer>
                          </Paper>
                        </Box>
                      ) : null}{' '}
                      {!requestedGroupManager || ownRequest ? (
                        <Box sx={{my: 2}}>
                          <Paper sx={{p: 2}}>
                            <Typography variant="body1">
                              Request is <b>pending</b> and can be reviewed by the following owners
                            </Typography>
                          </Paper>
                          {accessRequest.requested_group?.type != 'app_group' ||
                          !(accessRequest.requested_group as AppGroup).is_owner ? (
                            <Paper sx={{p: 2, mt: 1}}>
                              <Table size="small" aria-label="group owners">
                                <TableHead>
                                  <TableRow>
                                    <TableCell colSpan={3}>
                                      <Typography variant="h6" color="text.accent">
                                        {accessRequest.requested_group?.name}{' '}
                                        {accessRequest.requested_group?.type == 'role_group'
                                          ? 'Owners'
                                          : 'Group Owners'}
                                      </Typography>
                                    </TableCell>
                                  </TableRow>
                                  <TableRow>
                                    <TableCell>Name</TableCell>
                                    <TableCell>Email</TableCell>
                                    <TableCell>
                                      <Box
                                        sx={{
                                          display: 'flex',
                                          justifyContent: 'flex-end',
                                          alignItems: 'right',
                                        }}>
                                        <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                                        Total Owners: {Object.keys(ownerships).length}
                                      </Box>
                                    </TableCell>
                                  </TableRow>
                                </TableHead>
                                <TableBody>
                                  {Object.keys(ownerships).length > 0 ? (
                                    Object.entries(ownerships)
                                      .sort(sortGroupMembers)
                                      .map(([userId, users]: [string, Array<OktaUserGroupMember>]) => (
                                        <TableRow key={'owner' + userId}>
                                          <TableCell>
                                            <Link
                                              to={`/users/${users[0].active_user?.email.toLowerCase()}`}
                                              sx={{
                                                textDecoration: 'none',
                                                color: 'inherit',
                                              }}
                                              component={RouterLink}>
                                              {displayUserName(users[0].active_user)}
                                            </Link>
                                          </TableCell>
                                          <TableCell colSpan={2}>
                                            <Link
                                              to={`/users/${users[0].active_user?.email.toLowerCase()}`}
                                              sx={{
                                                textDecoration: 'none',
                                                color: 'inherit',
                                              }}
                                              component={RouterLink}>
                                              {users[0].active_user?.email.toLowerCase()}
                                            </Link>
                                          </TableCell>
                                        </TableRow>
                                      ))
                                  ) : (
                                    <EmptyListEntry cellProps={{colSpan: 3}} />
                                  )}
                                </TableBody>
                              </Table>
                            </Paper>
                          ) : null}
                          {accessRequest.requested_group?.type == 'app_group' ? (
                            <Paper sx={{p: 2, mt: 1}}>
                              <Table size="small" aria-label="app owners">
                                <TableHead>
                                  <TableRow>
                                    <TableCell colSpan={3}>
                                      <Typography variant="h6" color="text.accent">
                                        {((accessRequest.requested_group ?? {}) as AppGroup).app?.name}
                                        {' App Owners'}
                                      </Typography>
                                    </TableCell>
                                  </TableRow>
                                  <TableRow>
                                    <TableCell>Name</TableCell>
                                    <TableCell>Email</TableCell>
                                    <TableCell>
                                      <Box
                                        sx={{
                                          display: 'flex',
                                          justifyContent: 'flex-end',
                                          alignItems: 'right',
                                        }}>
                                        <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                                        Total Owners: {Object.keys(appOwnerships).length}
                                      </Box>
                                    </TableCell>
                                  </TableRow>
                                </TableHead>
                                <TableBody>
                                  {Object.keys(appOwnerships).length > 0 ? (
                                    Object.entries(appOwnerships)
                                      .sort(sortGroupMembers)
                                      .map(([userId, users]: [string, Array<OktaUserGroupMember>]) => (
                                        <TableRow key={'owner' + userId}>
                                          <TableCell>
                                            <Link
                                              to={`/users/${users[0].active_user?.email.toLowerCase()}`}
                                              sx={{
                                                textDecoration: 'none',
                                                color: 'inherit',
                                              }}
                                              component={RouterLink}>
                                              {displayUserName(users[0].active_user)}
                                            </Link>
                                          </TableCell>
                                          <TableCell colSpan={2}>
                                            <Link
                                              to={`/users/${users[0].active_user?.email.toLowerCase()}`}
                                              sx={{
                                                textDecoration: 'none',
                                                color: 'inherit',
                                              }}
                                              component={RouterLink}>
                                              {users[0].active_user?.email.toLowerCase()}
                                            </Link>
                                          </TableCell>
                                        </TableRow>
                                      ))
                                  ) : (
                                    <EmptyListEntry cellProps={{colSpan: 3}} />
                                  )}
                                </TableBody>
                              </Table>
                            </Paper>
                          ) : null}
                          {Object.keys(ownerships).length == 0 &&
                          (accessRequest.requested_group?.type != 'app_group' || appOwnershipsArray.length == 0) ? (
                            <Paper sx={{p: 2, mt: 1}}>
                              <Table size="small" aria-label="app owners">
                                <TableHead>
                                  <TableRow>
                                    <TableCell colSpan={3}>
                                      <Typography variant="h6" color="text.accent">
                                        {ACCESS_APP_RESERVED_NAME}
                                        {' Admins'}
                                      </Typography>
                                    </TableCell>
                                  </TableRow>
                                  <TableRow>
                                    <TableCell>Name</TableCell>
                                    <TableCell>Email</TableCell>
                                    <TableCell>
                                      <Box
                                        sx={{
                                          display: 'flex',
                                          justifyContent: 'flex-end',
                                          alignItems: 'right',
                                        }}>
                                        <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                                        Total Owners: {Object.keys(accessAppOwnerships).length}
                                      </Box>
                                    </TableCell>
                                  </TableRow>
                                </TableHead>
                                <TableBody>
                                  {Object.keys(accessAppOwnerships).length > 0 ? (
                                    Object.entries(accessAppOwnerships)
                                      .sort(sortGroupMembers)
                                      .map(([userId, users]: [string, Array<OktaUserGroupMember>]) => (
                                        <TableRow key={'owner' + userId}>
                                          <TableCell>
                                            <Link
                                              to={`/users/${users[0].active_user?.email.toLowerCase()}`}
                                              sx={{
                                                textDecoration: 'none',
                                                color: 'inherit',
                                              }}
                                              component={RouterLink}>
                                              {displayUserName(users[0].active_user)}
                                            </Link>
                                          </TableCell>
                                          <TableCell colSpan={2}>
                                            <Link
                                              to={`/users/${users[0].active_user?.email.toLowerCase()}`}
                                              sx={{
                                                textDecoration: 'none',
                                                color: 'inherit',
                                              }}
                                              component={RouterLink}>
                                              {users[0].active_user?.email.toLowerCase()}
                                            </Link>
                                          </TableCell>
                                        </TableRow>
                                      ))
                                  ) : (
                                    <EmptyListEntry cellProps={{colSpan: 3}} />
                                  )}
                                </TableBody>
                              </Table>
                            </Paper>
                          ) : null}
                        </Box>
                      ) : null}
                    </>
                  ) : (
                    <Paper sx={{p: 2, my: 2}}>
                      <Typography variant="body1">
                        {accessRequest.resolver == null ? (
                          <b>Access</b>
                        ) : (
                          <Link
                            to={`/users/${accessRequest.resolver?.email.toLowerCase()}`}
                            sx={{
                              textDecoration: 'none',
                              color: 'inherit',
                            }}
                            component={RouterLink}>
                            <b>{accessRequest.resolver?.email.toLowerCase()}</b>
                          </Link>
                        )}
                        {accessRequest.status == 'APPROVED' ? (
                          <>
                            {' approved the request ending '}
                            <b>
                              {accessRequest.approval_ending_at == null
                                ? 'never'
                                : dayjs(accessRequest.approval_ending_at).startOf('second').fromNow()}
                            </b>
                          </>
                        ) : (
                          ' rejected the request '
                        )}
                      </Typography>
                      <Typography variant="body2">
                        <b>Reason:</b>{' '}
                        {accessRequest.resolution_reason ? accessRequest.resolution_reason : 'No reason given'}
                      </Typography>
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
