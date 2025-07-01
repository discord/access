import React from 'react';
import {Link as RouterLink, useParams, useNavigate} from 'react-router-dom';

import Link from '@mui/material/Link';
import Container from '@mui/material/Container';
import Grid from '@mui/material/Grid';
import Box from '@mui/material/Box';
import Paper from '@mui/material/Paper';
import Button from '@mui/material/Button';
import Stack from '@mui/material/Stack';
import Avatar from '@mui/material/Avatar';
import Typography from '@mui/material/Typography';
import Divider from '@mui/material/Divider';
import RoleRequestIcon from '@mui/icons-material/WorkHistory';
import AlertIcon from '@mui/icons-material/Campaign';
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
import InfoOutlined from '@mui/icons-material/InfoOutlined';
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

import RoleMembers from './RoleMembers';
import {
  groupBy,
  displayUserName,
  minTagTime,
  minTagTimeGroups,
  ownerCantAddSelf,
  requiredReason,
  requiredReasonGroups,
} from '../../helpers';
import {useCurrentUser} from '../../authentication';
import {canManageGroup, isAccessAdmin, ACCESS_APP_RESERVED_NAME} from '../../authorization';
import {
  useGetRoleRequestById,
  useGetGroupById,
  useGetAppById,
  useResolveRoleRequestById,
  ResolveRoleRequestByIdError,
  ResolveRoleRequestByIdVariables,
  useGetGroupRoleAudits,
} from '../../api/apiComponents';
import {
  App,
  AppGroup,
  OktaGroup,
  OktaUserGroupMember,
  PolymorphicGroup,
  ResolveRoleRequest,
  RoleGroup,
  RoleRequest,
  Tag,
} from '../../api/apiSchemas';

import NotFound from '../NotFound';
import Loading from '../../components/Loading';
import ChangeTitle from '../../tab-title';
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

const UNTIL_ID_TO_LABELS: Record<string, string> = {
  '43200': '12 Hours',
  '432000': '5 Days',
  '1209600': 'Two Weeks',
  '2592000': '30 Days',
  '7776000': '90 Days',
  indefinite: 'Indefinite',
  custom: 'Custom',
} as const;

const UNTIL_JUST_NUMERIC_ID_TO_LABELS: Record<string, string> = {
  '43200': '12 Hours',
  '432000': '5 Days',
  '1209600': 'Two Weeks',
  '2592000': '30 Days',
  '7776000': '90 Days',
} as const;

const UNTIL_OPTIONS = Object.entries(UNTIL_ID_TO_LABELS).map(([id, label], index) => ({id: id, label: label}));

function ComputeConstraints(roleRequest: RoleRequest) {
  const group = roleRequest.requested_group ?? null;

  if (group == null) {
    return [null, null];
  }

  let timeLimit = minTagTime(
    group.active_group_tags ? group.active_group_tags.map((tagMap) => tagMap.active_tag!) : [],
    roleRequest.request_ownership!,
  );

  let reason = requiredReason(
    group.active_group_tags ? group.active_group_tags?.map((tagMap) => tagMap.active_tag!) : [],
    roleRequest.request_ownership!,
  );

  if (group.type == 'role_group' && !roleRequest.request_ownership) {
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

export default function ReadRoleRequest() {
  const navigate = useNavigate();
  const {id} = useParams();

  const currentUser = useCurrentUser();
  const admin = isAccessAdmin(currentUser);

  const [until, setUntil] = React.useState<string | null>(null);
  const [requestError, setRequestError] = React.useState('');
  const [approved, setApproved] = React.useState<boolean | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  const {data, isError, isLoading} = useGetRoleRequestById({
    pathParams: {roleRequestId: id ?? ''},
  });

  const roleRequest = data ?? ({} as RoleRequest);

  const ownRequest = roleRequest.requester?.id == currentUser.id;

  const requestEndingAt = dayjs(roleRequest.request_ending_at);
  // round the delta to adjust based on partial seconds
  const requestedUntilDelta =
    roleRequest.request_ending_at == null
      ? null
      : Math.round(requestEndingAt.diff(dayjs(roleRequest.created_at), 'second') / 100) * 100;
  const requestedUntil =
    requestedUntilDelta == null
      ? 'indefinite'
      : requestedUntilDelta in UNTIL_ID_TO_LABELS
        ? requestedUntilDelta.toString()
        : 'custom';

  // Check to see if current user is a blocked group owner
  const ownedGroup = currentUser.active_group_ownerships
    ?.map((group) => group.active_group!.id)
    .includes(roleRequest.requested_group?.id);
  const roleMembers = roleRequest.requester_role?.active_user_memberships?.map((user) => user.active_user!.id) ?? [];
  const tags: Tag[] = (roleRequest.requested_group?.active_group_tags ?? []).reduce((out, t) => {
    if (t.active_tag) {
      out.push(t.active_tag);
      return out;
    } else {
      return out;
    }
  }, new Array<Tag>());
  const tagged =
    (ownerCantAddSelf(tags, false) && !roleRequest.request_ownership) ||
    (ownerCantAddSelf(tags, true) && roleRequest.request_ownership);
  const blocked = ownedGroup && roleMembers.includes(currentUser.id) && tagged;
  const manager = canManageGroup(currentUser, roleRequest.requested_group);
  const requestedGroupManager = manager && !blocked;

  const complete = (
    completedAccessRequest: ResolveRoleRequest | undefined,
    error: ResolveRoleRequestByIdError | null,
    variables: ResolveRoleRequestByIdVariables,
    context: any,
  ) => {
    setSubmitting(false);
    if (error != null) {
      setRequestError(error.payload.toString());
    } else {
      navigate(0);
    }
  };

  const putResolveRequest = useResolveRoleRequestById({
    onSettled: complete,
  });

  const {data: groupData} = useGetGroupById(
    {
      pathParams: {groupId: roleRequest.requested_group?.id ?? ''},
    },
    {
      enabled: roleRequest.requested_group != null && (!requestedGroupManager || ownRequest),
    },
  );

  const group = groupData ?? ({} as PolymorphicGroup);

  const constraints = ComputeConstraints(roleRequest);

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

  let ownerships = groupBy(group.active_user_ownerships, (m) => m.active_user?.id);
  const ownerIds = Object.keys(ownerships);

  // If Access admin and all group owners are blocked, add a note
  const adminNoteForBlocked = tagged && admin && ownerIds.every((v) => roleMembers.includes(v)) && ownerIds.length > 0;

  const {data: appData} = useGetAppById(
    {
      pathParams: {
        appId: ((roleRequest.requested_group ?? {}) as AppGroup).app?.id ?? '',
      },
    },
    {
      enabled: roleRequest.requested_group?.type == 'app_group' && (!requestedGroupManager || ownRequest),
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
        roleRequest.requested_group != null &&
        (!requestedGroupManager || ownRequest) &&
        group.active_user_ownerships?.length == 0 &&
        (roleRequest.requested_group?.type != 'app_group' || appOwnershipsArray.length == 0),
    },
  );

  const accessApp = accessAppData ?? ({} as App);

  const accessAppOwnerships = groupBy(
    (accessApp.active_owner_app_groups ?? []).map((appGroup) => appGroup.active_user_memberships ?? []).flat(),
    (m) => m.active_user?.id,
  );

  // Fetch role/group audit data for the requester role and requested group
  const {data: groupRoleAuditsData} = useGetGroupRoleAudits({
    queryParams: {
      role_id: roleRequest.requester_role?.id ?? '',
      group_id: roleRequest.requested_group?.id ?? '',
      per_page: 50,
      order_by: 'created_at',
      order_desc: true,
    },
  });
  const groupRoleAudits = groupRoleAuditsData?.results ?? [];

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
    } as ResolveRoleRequest;

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
      pathParams: {roleRequestId: roleRequest.id},
    });
  };

  return (
    <React.Fragment>
      <ChangeTitle
        title={`Request: ${roleRequest.requester_role!.name} ${roleRequest.request_ownership ? 'ownership of' : 'membership to'} ${roleRequest.requested_group!.name}`}
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
                alt={roleRequest.status}
                sx={{
                  bgcolor:
                    roleRequest.status == 'PENDING'
                      ? 'primary.main'
                      : roleRequest.status == 'APPROVED'
                        ? 'success.main'
                        : 'error.main',
                  width: 220,
                  height: 220,
                }}
                variant={'rounded' as any}>
                {roleRequest.status == 'PENDING' ? (
                  <PendingIcon
                    sx={{
                      width: 220,
                      height: 220,
                    }}
                  />
                ) : roleRequest.status == 'APPROVED' ? (
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
                    {(roleRequest.requester_role?.deleted_at ?? null) != null ? (
                      <Link
                        to={`/groups/${roleRequest.requester_role?.id ?? ''}`}
                        sx={{textDecoration: 'line-through', color: 'inherit', fontWeight: 500}}
                        component={RouterLink}>
                        {roleRequest.requester_role?.name ?? ''}
                      </Link>
                    ) : (
                      <Link
                        to={`/groups/${roleRequest.requester_role?.name ?? ''}`}
                        sx={{textDecoration: 'none', color: 'inherit', fontWeight: 500}}
                        component={RouterLink}>
                        {roleRequest.requester_role?.name ?? ''}
                      </Link>
                    )}
                  </Typography>
                  <Typography variant="h5">
                    is requesting
                    {roleRequest.request_ownership ? (
                      <>
                        <Box component="span" sx={{color: 'primary.main', fontWeight: 'bold'}}>
                          <> ownership </>
                        </Box>{' '}
                        of
                      </>
                    ) : (
                      ' membership to '
                    )}
                  </Typography>
                  <Typography variant="h4">
                    {(roleRequest.requested_group?.deleted_at ?? null) != null ? (
                      <Link
                        to={`/groups/${roleRequest.requested_group?.id ?? ''}`}
                        sx={{textDecoration: 'line-through', color: 'inherit', fontWeight: 500}}
                        component={RouterLink}>
                        {roleRequest.requested_group?.name ?? ''}
                      </Link>
                    ) : (
                      <Link
                        to={`/groups/${roleRequest.requested_group?.name ?? ''}`}
                        sx={{textDecoration: 'none', color: 'inherit', fontWeight: 500}}
                        component={RouterLink}>
                        {roleRequest.requested_group?.name ?? ''}
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
                    <b>{roleRequest.status}</b>
                  </Typography>
                </Grid>
              </Grid>
            </Paper>
          </Grid>
          {/* Access History Section */}
          <Grid item xs={12}>
            <AccessHistory
              subjectType="role"
              subjectName={roleRequest.requester_role?.name ?? ''}
              groupName={roleRequest.requested_group?.name ?? ''}
              auditHistory={groupRoleAudits}
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
                  <span title={roleRequest.created_at}>
                    {dayjs(roleRequest.created_at).startOf('second').fromNow()}
                  </span>
                </TimelineOppositeContent>
                <TimelineSeparator>
                  <TimelineConnector />
                  <TimelineDot color="primary">
                    <RoleRequestIcon />
                  </TimelineDot>
                  <TimelineConnector />
                </TimelineSeparator>
                <TimelineContent>
                  <Paper sx={{p: 2, my: 1}}>
                    <Typography variant="body1">
                      {(roleRequest.requester?.deleted_at ?? null) != null ? (
                        <Link
                          to={`/users/${roleRequest.requester?.id}`}
                          sx={{
                            textDecoration: 'line-through',
                            color: 'inherit',
                          }}
                          component={RouterLink}>
                          <b>{roleRequest.requester?.email.toLowerCase()}</b>
                        </Link>
                      ) : (
                        <Link
                          to={`/users/${roleRequest.requester?.email.toLowerCase()}`}
                          sx={{
                            textDecoration: 'none',
                            color: 'inherit',
                          }}
                          component={RouterLink}>
                          <b>{roleRequest.requester?.email.toLowerCase()}</b>
                        </Link>
                      )}
                      {roleRequest.status == 'PENDING' ? ' is requesting that ' : ' requested that '}
                      {(roleRequest.requester_role?.deleted_at ?? null) != null ? (
                        <Link
                          to={`/groups/${roleRequest.requester_role?.id}`}
                          sx={{
                            textDecoration: 'line-through',
                            color: 'inherit',
                          }}
                          component={RouterLink}>
                          <b>{roleRequest.requester_role?.name}</b>
                        </Link>
                      ) : (
                        <Link
                          to={`/groups/${roleRequest.requester_role?.name}`}
                          sx={{
                            textDecoration: 'none',
                            color: 'inherit',
                          }}
                          component={RouterLink}>
                          <b>{roleRequest.requester_role?.name}</b>
                        </Link>
                      )}{' '}
                      {roleRequest.request_ownership ? 'is added as an owner of ' : 'is added as a member of '}
                      {(roleRequest.requested_group?.deleted_at ?? null) != null ? (
                        <Link
                          to={`/groups/${roleRequest.requested_group?.id}`}
                          sx={{
                            textDecoration: 'line-through',
                            color: 'inherit',
                          }}
                          component={RouterLink}>
                          <b>{roleRequest.requested_group?.name}</b>
                        </Link>
                      ) : (
                        <Link
                          to={`/groups/${roleRequest.requested_group?.name}`}
                          sx={{
                            textDecoration: 'none',
                            color: 'inherit',
                          }}
                          component={RouterLink}>
                          <b>{roleRequest.requested_group?.name}</b>
                        </Link>
                      )}{' '}
                      ending{' '}
                      <b>
                        {roleRequest.request_ending_at == null
                          ? 'never'
                          : dayjs(roleRequest.request_ending_at).startOf('second').fromNow()}
                      </b>
                      {'.'}
                    </Typography>
                    <Typography variant="body2" sx={{pt: 1}}>
                      <b>Reason:</b> {roleRequest.request_reason ? roleRequest.request_reason : 'No reason given'}
                    </Typography>
                  </Paper>
                  {manager && blocked ? (
                    <Paper sx={{p: 2}}>
                      <Stack alignItems="center" direction="row" gap={2}>
                        <AlertIcon fontSize="large" sx={{color: 'primary.main'}} />
                        <Typography display="inline" variant="body1">
                          <b>
                            While you own group {group.name}, you are blocked from responding to this request by group
                            tags. The request has been forwarded to other group owners or Access admins.
                          </b>
                        </Typography>
                      </Stack>
                    </Paper>
                  ) : (
                    <>
                      {adminNoteForBlocked ? (
                        <Paper sx={{p: 2, mb: 1}}>
                          <Stack alignItems="center" direction="row" gap={2}>
                            <AlertIcon fontSize="large" sx={{color: 'primary.main'}} />
                            <Typography display="inline" variant="body1">
                              <b>
                                All owners of group {group.name} are blocked from approving this request due to group
                                tags. As an Access admin, you may respond to it on their behalf.
                              </b>
                            </Typography>
                          </Stack>
                        </Paper>
                      ) : (
                        <></>
                      )}
                      <Paper sx={{p: 2}}>
                        <RoleMembers
                          rows={roleRequest.requester_role?.active_user_memberships ?? []}
                          roleName={roleRequest.requester_role?.name ?? ''}
                          groupName={roleRequest.requested_group?.name ?? ''}
                          owner={roleRequest.request_ownership ?? false}
                        />
                        <Typography display="inline" variant="body1" sx={{pl: 1}}>
                          If approved, everyone who is a member of the role will be added to the group.
                        </Typography>
                      </Paper>
                    </>
                  )}
                </TimelineContent>
              </TimelineItem>
              <TimelineItem>
                <TimelineOppositeContent sx={{m: 'auto 0'}} align="right">
                  {roleRequest.resolved_at != null ? (
                    <span title={roleRequest.resolved_at}>
                      {dayjs(roleRequest.resolved_at).startOf('second').fromNow()}
                    </span>
                  ) : null}
                </TimelineOppositeContent>
                <TimelineSeparator>
                  <TimelineConnector />
                  {roleRequest.status == 'PENDING' ? (
                    <TimelineDot color="primary">
                      <PendingIcon />
                    </TimelineDot>
                  ) : roleRequest.status == 'APPROVED' ? (
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
                  {roleRequest.status == 'PENDING' ? (
                    <>
                      {requestedGroupManager || ownRequest ? (
                        <Box>
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
                              {requestError != '' ? (
                                <Alert severity="error" sx={{my: 1}}>
                                  {requestError}
                                </Alert>
                              ) : null}
                              {!ownRequest ? (
                                <FormControl fullWidth>
                                  <Grid container>
                                    <Grid item xs={12}>
                                      <Typography variant="subtitle1" color="text.accent">
                                        {timeLimit
                                          ? (roleRequest.request_ownership ? 'Ownership of ' : 'Membership to ') +
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
                                            selected: roleRequest.request_ownership,
                                          },
                                          {
                                            id: 'member',
                                            label: 'Member',
                                            selected: !roleRequest.request_ownership,
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
                              Request is <b>pending</b> and can be reviewed by the following owners or by Access admins
                            </Typography>
                          </Paper>
                          {roleRequest.requested_group?.type != 'app_group' ||
                          !(roleRequest.requested_group as AppGroup).is_owner ? (
                            <Paper sx={{p: 2, mt: 1}}>
                              <Table size="small" aria-label="group owners">
                                <TableHead>
                                  <TableRow>
                                    <TableCell colSpan={3}>
                                      <Typography variant="h6" color="text.accent">
                                        {roleRequest.requested_group?.name}{' '}
                                        {roleRequest.requested_group?.type == 'role_group' ? 'Owners' : 'Group Owners'}
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
                                        Total Owners: {ownerIds.length}
                                      </Box>
                                    </TableCell>
                                  </TableRow>
                                </TableHead>
                                <TableBody>
                                  {ownerIds.length > 0 ? (
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
                                    <TableRow key="owners">
                                      <TableCell colSpan={3}>
                                        <Typography variant="body2" color="text.secondary">
                                          None
                                        </Typography>
                                      </TableCell>
                                    </TableRow>
                                  )}
                                </TableBody>
                              </Table>
                            </Paper>
                          ) : null}
                          {roleRequest.requested_group?.type == 'app_group' ? (
                            <Paper sx={{p: 2, mt: 1}}>
                              <Table size="small" aria-label="app owners">
                                <TableHead>
                                  <TableRow>
                                    <TableCell colSpan={3}>
                                      <Typography variant="h6" color="text.accent">
                                        {((roleRequest.requested_group ?? {}) as AppGroup).app?.name}
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
                                    <TableRow key="owners">
                                      <TableCell colSpan={3}>
                                        <Typography variant="body2" color="text.secondary">
                                          None
                                        </Typography>
                                      </TableCell>
                                    </TableRow>
                                  )}
                                </TableBody>
                              </Table>
                            </Paper>
                          ) : null}
                          {ownerIds.length == 0 &&
                          (roleRequest.requested_group?.type != 'app_group' || appOwnershipsArray.length == 0) ? (
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
                                    <TableRow key="owners">
                                      <TableCell colSpan={3}>
                                        <Typography variant="body2" color="text.secondary">
                                          None
                                        </Typography>
                                      </TableCell>
                                    </TableRow>
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
                        {roleRequest.resolver == null ? (
                          <b>Access</b>
                        ) : (
                          <Link
                            to={`/users/${roleRequest.resolver?.email.toLowerCase()}`}
                            sx={{
                              textDecoration: 'none',
                              color: 'inherit',
                            }}
                            component={RouterLink}>
                            <b>{roleRequest.resolver?.email.toLowerCase()}</b>
                          </Link>
                        )}
                        {roleRequest.status == 'APPROVED' ? (
                          <>
                            {' approved the request ending '}
                            <b>
                              {roleRequest.approval_ending_at == null
                                ? 'never'
                                : dayjs(roleRequest.approval_ending_at).startOf('second').fromNow()}
                            </b>
                          </>
                        ) : (
                          ' rejected the request '
                        )}
                      </Typography>
                      <Typography variant="body2">
                        <b>Reason:</b>{' '}
                        {roleRequest.resolution_reason ? roleRequest.resolution_reason : 'No reason given'}
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
