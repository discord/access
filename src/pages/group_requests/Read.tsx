import React from 'react';
import {Link as RouterLink, useNavigate, useParams} from 'react-router-dom';

import ApprovedIcon from '@mui/icons-material/CheckCircleOutline';
import GroupRequestIcon from '@mui/icons-material/GroupAdd';
import PendingIcon from '@mui/icons-material/HelpOutline';
import RejectedIcon from '@mui/icons-material/HighlightOff';
import Timeline from '@mui/lab/Timeline';
import TimelineConnector from '@mui/lab/TimelineConnector';
import TimelineContent from '@mui/lab/TimelineContent';
import TimelineDot from '@mui/lab/TimelineDot';
import TimelineItem from '@mui/lab/TimelineItem';
import TimelineOppositeContent, {timelineOppositeContentClasses} from '@mui/lab/TimelineOppositeContent';
import TimelineSeparator from '@mui/lab/TimelineSeparator';
import Alert from '@mui/material/Alert';
import Autocomplete from '@mui/material/Autocomplete';
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
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import {
  AutocompleteElement,
  DatePickerElement,
  FormContainer,
  SelectElement,
  TextFieldElement,
  useFormContext,
} from 'react-hook-form-mui';
import InputLabel from '@mui/material/InputLabel';
import MenuItem from '@mui/material/MenuItem';
import Select from '@mui/material/Select';
import {Controller} from 'react-hook-form';

import dayjs, {Dayjs} from 'dayjs';
import IsSameOrBefore from 'dayjs/plugin/isSameOrBefore';
import RelativeTime from 'dayjs/plugin/relativeTime';

import {
  ResolveGroupRequestByIdError,
  ResolveGroupRequestByIdVariables,
  useGetAppById,
  useGetApps,
  useGetGroupRequestById,
  useGetTags,
  useResolveGroupRequestById,
} from '../../api/apiComponents';
import {App, GroupRequest, ResolveGroupRequest, Tag} from '../../api/apiSchemas';
import {useCurrentUser} from '../../authentication';
import {isAccessAdmin} from '../../authorization';
import {displayUserName, minTagTime} from '../../helpers';

import Loading from '../../components/Loading';
import accessConfig from '../../config/accessConfig';
import ChangeTitle from '../../tab-title';
import NotFound from '../NotFound';

dayjs.extend(RelativeTime);
dayjs.extend(IsSameOrBefore);

const GROUP_TYPE_ID_TO_LABELS: Record<string, string> = {
  okta_group: 'Group',
  app_group: 'App Group',
  role_group: 'Role',
} as const;

const GROUP_TYPE_OPTIONS = Object.entries(GROUP_TYPE_ID_TO_LABELS).map(([id, label]) => ({id, label}));

const UNTIL_ID_TO_LABELS: Record<string, string> = accessConfig.ACCESS_TIME_LABELS;
const UNTIL_OPTIONS = Object.entries(UNTIL_ID_TO_LABELS).map(([id, label]) => ({id, label}));
const UNTIL_JUST_NUMERIC_ID_TO_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries(UNTIL_ID_TO_LABELS).filter(([key]) => !isNaN(Number(key))),
);

const APP_GROUP_PREFIX = accessConfig.APP_GROUP_NAME_PREFIX;
const APP_NAME_APP_GROUP_SEPARATOR = accessConfig.APP_NAME_GROUP_NAME_SEPARATOR;
const ROLE_GROUP_PREFIX = accessConfig.ROLE_GROUP_NAME_PREFIX;

const RFC822_FORMAT = 'ddd, DD MMM YYYY HH:mm:ss ZZ';

interface OwnershipEndingFieldProps {
  ownershipTimeLimit: number | null;
  ownershipUntil: string | null;
  setOwnershipUntil: (v: string | null) => void;
}

function OwnershipEndingField({ownershipTimeLimit, ownershipUntil, setOwnershipUntil}: OwnershipEndingFieldProps) {
  const {control, setValue} = useFormContext();

  const [availableUntilOptions, defaultUntilId] = React.useMemo<[Array<{id: string; label: string}>, string]>(() => {
    if (ownershipTimeLimit == null) {
      return [UNTIL_OPTIONS, accessConfig.DEFAULT_ACCESS_TIME];
    }
    const [lastId, filtered] = filterUntilLabels(ownershipTimeLimit);
    return [filtered, lastId];
  }, [ownershipTimeLimit]);

  React.useEffect(() => {
    if (ownershipUntil == null || ownershipUntil === 'indefinite' || ownershipUntil === 'custom') return;
    const seconds = parseInt(ownershipUntil, 10);
    if (!isNaN(seconds) && ownershipTimeLimit != null && seconds > ownershipTimeLimit) {
      setOwnershipUntil(defaultUntilId ?? null);
      setValue('resolved_ownership_ending_at', defaultUntilId ?? '');
    }
  }, [ownershipTimeLimit, ownershipUntil, defaultUntilId, setValue, setOwnershipUntil]);

  return (
    <FormControl margin="normal" fullWidth>
      <InputLabel id="ownership-ending-at-label">Ownership Ending At</InputLabel>
      <Controller
        name="resolved_ownership_ending_at"
        control={control}
        render={({field}) => (
          <Select
            {...field}
            labelId="ownership-ending-at-label"
            label="Ownership Ending At"
            onChange={(e) => {
              field.onChange(e);
              setOwnershipUntil(e.target.value as string);
            }}>
            {availableUntilOptions.map(({id, label}) => (
              <MenuItem key={id} value={id}>
                {label}
              </MenuItem>
            ))}
          </Select>
        )}
      />
    </FormControl>
  );
}

function filterUntilLabels(timeLimit: number): [string, Array<{id: string; label: string}>] {
  const filteredUntil = Object.keys(UNTIL_JUST_NUMERIC_ID_TO_LABELS)
    .filter((key) => Number(key) <= timeLimit)
    .reduce(
      (obj, key) => {
        obj[key] = UNTIL_JUST_NUMERIC_ID_TO_LABELS[key];
        return obj;
      },
      {} as Record<string, string>,
    );

  const filteredLabels = Object.entries(Object.assign({}, filteredUntil, {custom: 'Custom'})).map(([id, label]) => ({
    id,
    label,
  }));

  return [Object.keys(filteredUntil).at(-1)!, filteredLabels];
}

interface ResolveRequestForm {
  resolved_group_name: string;
  resolved_group_description: string;
  resolved_group_type: string;
  resolved_app?: App;
  resolved_ownership_ending_at?: string;
  resolved_ownership_ending_at_custom?: string;
  resolution_reason?: string;
}

export default function ReadGroupRequest() {
  const navigate = useNavigate();
  const {id} = useParams();

  const currentUser = useCurrentUser();
  const admin = isAccessAdmin(currentUser);

  const [requestError, setRequestError] = React.useState('');
  const [approved, setApproved] = React.useState<boolean | null>(null);
  const [submitting, setSubmitting] = React.useState(false);
  const [groupType, setGroupType] = React.useState<string>('okta_group');
  const [appName, setAppName] = React.useState('');
  const [appSearchInput, setAppSearchInput] = React.useState('');
  const [selectedTags, setSelectedTags] = React.useState<Tag[]>([]);
  const [tagSearchInput, setTagSearchInput] = React.useState('');
  const [ownershipUntil, setOwnershipUntil] = React.useState<string | null>(null);

  const ownershipTimeLimit = React.useMemo<number | null>(() => minTagTime(selectedTags, true), [selectedTags]);

  const {data, isError, isLoading} = useGetGroupRequestById({
    pathParams: {groupRequestId: id ?? ''},
  });

  const groupRequest: GroupRequest = data ?? ({} as GroupRequest);

  const ownRequest = groupRequest.requester?.id == currentUser.id;

  const requestedGroupType = groupRequest.requested_group_type ?? 'okta_group';
  const requestedAppId = groupRequest.requested_app_id ?? null;
  const requestedTagNames: string[] = groupRequest.requested_group_tags ?? [];

  const [typesSeeded, setTypesSeeded] = React.useState(false);
  React.useEffect(() => {
    if (!typesSeeded && data?.requested_group_type) {
      setGroupType(data.requested_group_type);
      if (data.requested_ownership_ending_at) {
        const delta =
          Math.round(dayjs(data.requested_ownership_ending_at).diff(dayjs(data.created_at), 'second') / 100) * 100;
        setOwnershipUntil(delta in UNTIL_ID_TO_LABELS ? delta.toString() : 'custom');
      }
      setTypesSeeded(true);
    }
  }, [data, typesSeeded]);

  const {data: requestedAppData, isLoading: isAppLoading} = useGetAppById(
    {pathParams: {appId: requestedAppId ?? ''}},
    {enabled: requestedAppId != null && requestedGroupType === 'app_group'},
  );
  const [appSeeded, setAppSeeded] = React.useState(false);
  React.useEffect(() => {
    if (!appSeeded && requestedAppData?.name) {
      setAppName(requestedAppData.name);
      setAppSeeded(true);
    }
  }, [requestedAppData, appSeeded]);

  const isAppOwner = React.useMemo<boolean>(() => {
    if (admin || ownRequest || requestedGroupType !== 'app_group' || !requestedAppData) {
      return false;
    }
    return (requestedAppData.active_owner_app_groups ?? []).some((appGroup) =>
      (appGroup.active_user_ownerships ?? []).some((membership) => membership.active_user?.id === currentUser.id),
    );
  }, [admin, ownRequest, requestedGroupType, requestedAppData, currentUser.id]);

  const {data: ownedAppsData} = useGetApps({queryParams: {page: 0, per_page: 100, q: ''}}, {enabled: isAppOwner});

  const ownedAppIds = React.useMemo<Set<string>>(() => {
    const ids = new Set<string>();
    for (const app of ownedAppsData?.results ?? []) {
      const owns = (app.active_owner_app_groups ?? []).some((appGroup) =>
        (appGroup.active_user_ownerships ?? []).some((membership) => membership.active_user?.id === currentUser.id),
      );
      if (owns && app.id) {
        ids.add(app.id);
      }
    }
    if (isAppOwner && requestedAppId) {
      ids.add(requestedAppId);
    }
    return ids;
  }, [ownedAppsData, currentUser.id, isAppOwner, requestedAppId]);

  const canApprove = (admin || isAppOwner) && !ownRequest;
  const canResolve = canApprove || ownRequest;

  const {data: appSearchData} = useGetApps({
    queryParams: {page: 0, per_page: 10, q: appSearchInput},
  });

  const appSearchOptions = React.useMemo<App[]>(() => {
    const results = appSearchData?.results ?? [];
    if (isAppOwner) {
      return results.filter((app) => app.id != null && ownedAppIds.has(app.id));
    }
    return results;
  }, [appSearchData, isAppOwner, ownedAppIds]);

  const {data: tagSearchData} = useGetTags({
    queryParams: {page: 0, per_page: 10, q: tagSearchInput},
  });
  const tagSearchOptions = tagSearchData?.results ?? [];

  const {data: allTagsForSeeding} = useGetTags(
    {queryParams: {page: 0, per_page: 100, q: ''}},
    {enabled: requestedTagNames.length > 0},
  );
  const [tagsSeeded, setTagsSeeded] = React.useState(false);
  React.useEffect(() => {
    if (!tagsSeeded && requestedTagNames.length > 0 && allTagsForSeeding?.results) {
      const matched = allTagsForSeeding.results.filter((t) => requestedTagNames.includes(t.id));
      setSelectedTags(matched);
      setTagsSeeded(true);
    }
  }, [allTagsForSeeding, tagsSeeded, requestedTagNames.length]);

  const requestedTags = React.useMemo<Tag[]>(() => {
    if (!allTagsForSeeding?.results) return [];
    return allTagsForSeeding.results.filter((t) => requestedTagNames.includes(t.id));
  }, [allTagsForSeeding, requestedTagNames]);

  const resolvedTagIds: string[] =
    Array.isArray(groupRequest.resolved_group_tags) && groupRequest.resolved_group_tags.length > 0
      ? groupRequest.resolved_group_tags
      : [];
  const {data: allTagsForResolved} = useGetTags(
    {queryParams: {page: 0, per_page: 100, q: ''}},
    {enabled: groupRequest.status === 'APPROVED' && resolvedTagIds.length > 0},
  );
  const resolvedTags = React.useMemo<Tag[]>(() => {
    if (!allTagsForResolved?.results) return [];
    return allTagsForResolved.results.filter((t) => resolvedTagIds.includes(t.id));
  }, [allTagsForResolved, resolvedTagIds]);

  const complete = (
    completedRequest: ResolveGroupRequest | undefined,
    error: ResolveGroupRequestByIdError | null,
    variables: ResolveGroupRequestByIdVariables,
    context: any,
  ) => {
    setSubmitting(false);
    if (error != null) {
      setRequestError(error.payload.toString());
    } else {
      navigate(0);
    }
  };

  const putResolveRequest = useResolveGroupRequestById({
    onSettled: complete,
  });

  const approvedDetails = React.useMemo(() => {
    if (groupRequest.status !== 'APPROVED') return [];

    const safeRequestedTagNames = Array.isArray(requestedTagNames) ? requestedTagNames : [];
    const safeResolvedTagIds = Array.isArray(resolvedTagIds) ? resolvedTagIds : [];
    const requestedTagsSet = new Set(safeRequestedTagNames);
    const resolvedTagsSet = new Set(safeResolvedTagIds);

    const tagsChanged =
      resolvedTagIds.length > 0 &&
      (resolvedTagsSet.size !== requestedTagsSet.size ||
        safeResolvedTagIds.some((id) => !requestedTagsSet.has(id)) ||
        safeRequestedTagNames.some((id) => !resolvedTagsSet.has(id)));

    type ApprovedField =
      | {kind: 'text'; label: string; value: string; changed: boolean; from?: string}
      | {kind: 'tags'; label: string; resolvedTags: Tag[]; changed: boolean; requestedTagNames: string[]};

    const resolvedOwnership = groupRequest.resolved_ownership_ending_at || null;
    const requestedOwnership = groupRequest.requested_ownership_ending_at || null;
    let ownershipChanged: boolean;
    if (!resolvedOwnership && !requestedOwnership) {
      ownershipChanged = false;
    } else if (!resolvedOwnership || !requestedOwnership) {
      ownershipChanged = true;
    } else {
      const requestedDelta =
        Math.round(dayjs(requestedOwnership).diff(dayjs(groupRequest.created_at), 'second') / 100) * 100;
      const resolvedDelta =
        Math.round(dayjs(resolvedOwnership).diff(dayjs(groupRequest.resolved_at), 'second') / 100) * 100;
      ownershipChanged = requestedDelta !== resolvedDelta;
    }

    const fields: ApprovedField[] = [
      {
        kind: 'text',
        label: 'Name',
        value: (groupRequest.resolved_group_name || groupRequest.requested_group_name) ?? '—',
        changed:
          !!groupRequest.resolved_group_name && groupRequest.resolved_group_name !== groupRequest.requested_group_name,
        from: groupRequest.requested_group_name ?? '—',
      },
      {
        kind: 'text',
        label: 'Type',
        value:
          GROUP_TYPE_ID_TO_LABELS[groupRequest.resolved_group_type || requestedGroupType] ??
          groupRequest.resolved_group_type ??
          requestedGroupType,
        changed:
          !!groupRequest.resolved_group_type && groupRequest.resolved_group_type !== groupRequest.requested_group_type,
        from: GROUP_TYPE_ID_TO_LABELS[requestedGroupType] ?? requestedGroupType,
      },
      {
        kind: 'text',
        label: 'Description',
        value: (groupRequest.resolved_group_description || groupRequest.requested_group_description) ?? '—',
        changed:
          !!groupRequest.resolved_group_description &&
          groupRequest.resolved_group_description !== groupRequest.requested_group_description,
        from: groupRequest.requested_group_description ?? '—',
      },
      {
        kind: 'text',
        label: 'Ownership Ending',
        value: resolvedOwnership
          ? dayjs(resolvedOwnership).startOf('second').fromNow()
          : requestedOwnership
            ? dayjs(requestedOwnership).startOf('second').fromNow()
            : 'Indefinite',
        changed: ownershipChanged,
        from: requestedOwnership ? dayjs(requestedOwnership).startOf('second').fromNow() : 'Indefinite',
      },
      {
        kind: 'tags',
        label: 'Tags',
        resolvedTags: resolvedTagIds.length > 0 ? resolvedTags : requestedTags,
        changed: tagsChanged,
        requestedTagNames,
      },
    ];

    return fields;
  }, [groupRequest, resolvedTags, resolvedTagIds, requestedTagNames, requestedGroupType, requestedTags]);

  if (isError) {
    return <NotFound />;
  }

  if (isLoading || (requestedGroupType === 'app_group' && requestedAppId != null && isAppLoading)) {
    return <Loading />;
  }

  const strippedRequestedName = (() => {
    const raw = groupRequest.requested_group_name ?? '';
    if (requestedGroupType === 'role_group' && raw.startsWith(ROLE_GROUP_PREFIX)) {
      return raw.substring(ROLE_GROUP_PREFIX.length);
    }
    if (requestedGroupType === 'app_group' && requestedAppData?.name) {
      const prefix = APP_GROUP_PREFIX + requestedAppData.name + APP_NAME_APP_GROUP_SEPARATOR;
      if (raw.startsWith(prefix)) {
        return raw.substring(prefix.length);
      }
    }
    return raw;
  })();

  const defaultFormValues: ResolveRequestForm = {
    resolved_group_name: strippedRequestedName,
    resolved_group_description: groupRequest.requested_group_description ?? '',
    resolved_group_type: requestedGroupType,
    resolved_app: requestedAppData ?? undefined,
    resolved_ownership_ending_at: groupRequest.requested_ownership_ending_at
      ? (() => {
          const endingAt = groupRequest.requested_ownership_ending_at;
          const delta = Math.round(dayjs(endingAt).diff(dayjs(groupRequest.created_at), 'second') / 100) * 100;
          return delta in UNTIL_ID_TO_LABELS ? delta.toString() : 'custom';
        })()
      : 'indefinite',
    resolved_ownership_ending_at_custom: groupRequest.requested_ownership_ending_at ?? undefined,
    resolution_reason: '',
  };

  const submit = (responseForm: ResolveRequestForm) => {
    setSubmitting(true);

    let resolvedName = responseForm.resolved_group_name;
    switch (responseForm.resolved_group_type) {
      case 'role_group':
        resolvedName = ROLE_GROUP_PREFIX + resolvedName;
        break;
      case 'app_group':
        resolvedName =
          APP_GROUP_PREFIX + (responseForm.resolved_app?.name ?? appName) + APP_NAME_APP_GROUP_SEPARATOR + resolvedName;
        break;
    }

    const resolveRequest: ResolveGroupRequest = {
      approved: approved ?? false,
      resolved_group_name: resolvedName,
      resolved_group_description: responseForm.resolved_group_description,
      resolved_group_type: responseForm.resolved_group_type,
      resolved_app_id:
        responseForm.resolved_group_type === 'app_group' ? responseForm.resolved_app?.id ?? '' : undefined,
      resolved_group_tags: selectedTags.map((t) => t.id),
      resolution_reason: responseForm.resolution_reason ?? '',
    };

    switch (responseForm.resolved_ownership_ending_at) {
      case 'indefinite':
      case undefined:
        break;
      case 'custom':
        if (responseForm.resolved_ownership_ending_at_custom) {
          resolveRequest.resolved_ownership_ending_at = dayjs(responseForm.resolved_ownership_ending_at_custom).format(
            RFC822_FORMAT,
          );
        }
        break;
      default:
        resolveRequest.resolved_ownership_ending_at = dayjs()
          .add(parseInt(responseForm.resolved_ownership_ending_at, 10), 'seconds')
          .format(RFC822_FORMAT);
        break;
    }

    putResolveRequest.mutate({
      body: resolveRequest,
      pathParams: {groupRequestId: groupRequest.id},
    });
  };

  const requesterName = displayUserName(groupRequest.requester);
  const requestedGroupName = groupRequest.requested_group_name ?? 'Unknown Group';
  const titleGroupName =
    groupRequest.status === 'APPROVED' && groupRequest.resolved_group_name
      ? groupRequest.resolved_group_name
      : requestedGroupName;

  return (
    <React.Fragment>
      <ChangeTitle title={`Group Request: ${requesterName} — ${titleGroupName}`} />
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
                alt={groupRequest.status}
                sx={{
                  bgcolor:
                    groupRequest.status == 'PENDING'
                      ? 'primary.main'
                      : groupRequest.status == 'APPROVED'
                        ? 'success.main'
                        : 'error.main',
                  width: 220,
                  height: 220,
                }}
                variant={'rounded' as any}>
                {groupRequest.status == 'PENDING' ? (
                  <PendingIcon sx={{width: 220, height: 220}} />
                ) : groupRequest.status == 'APPROVED' ? (
                  <ApprovedIcon sx={{width: 220, height: 220}} />
                ) : (
                  <RejectedIcon sx={{width: 220, height: 220}} />
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
              }}>
              <Grid container>
                <Grid item xs={8} sx={{textAlign: 'center', wordBreak: 'break-word'}}>
                  <Typography variant="h4">
                    {(groupRequest.requester?.deleted_at ?? null) != null ? (
                      <Link
                        to={`/users/${groupRequest.requester?.id ?? ''}`}
                        sx={{textDecoration: 'line-through', color: 'inherit', fontWeight: 500}}
                        component={RouterLink}>
                        {requesterName}
                      </Link>
                    ) : (
                      <Link
                        to={`/users/${groupRequest.requester?.email.toLowerCase() ?? ''}`}
                        sx={{textDecoration: 'none', color: 'inherit', fontWeight: 500}}
                        component={RouterLink}>
                        {requesterName}
                      </Link>
                    )}
                  </Typography>
                  <Typography variant="h5">
                    {groupRequest.status == 'PENDING' ? 'is requesting' : 'requested'} the creation of
                  </Typography>
                  <Typography variant="h4" sx={{fontWeight: 500}}>
                    {groupRequest.status == 'APPROVED' ? (
                      <Link
                        to={`/groups/${titleGroupName}`}
                        sx={{textDecoration: 'none', color: 'inherit', fontWeight: 500}}
                        component={RouterLink}>
                        {titleGroupName}
                      </Link>
                    ) : (
                      requestedGroupName
                    )}
                  </Typography>
                </Grid>
                <Divider orientation="vertical" flexItem sx={{mr: '-1px'}} />
                <Grid
                  item
                  xs={4}
                  sx={{display: 'flex', flexDirection: 'column', justifyContent: 'center', textAlign: 'center'}}>
                  <Typography variant="h6">Status</Typography>
                  <Typography variant="h4">
                    <b>{groupRequest.status}</b>
                  </Typography>
                </Grid>
              </Grid>
            </Paper>
          </Grid>
          <Grid item xs={12}>
            <Timeline sx={{[`& .${timelineOppositeContentClasses.root}`]: {flex: 0.1}}}>
              <TimelineItem>
                <TimelineOppositeContent sx={{m: 'auto 0'}} align="right">
                  <span title={groupRequest.created_at}>
                    {dayjs(groupRequest.created_at).startOf('second').fromNow()}
                  </span>
                </TimelineOppositeContent>
                <TimelineSeparator>
                  <TimelineConnector />
                  <TimelineDot color="primary">
                    <GroupRequestIcon />
                  </TimelineDot>
                  <TimelineConnector />
                </TimelineSeparator>
                <TimelineContent>
                  <Paper sx={{p: 2, my: 1}}>
                    <Typography variant="body1">
                      {(groupRequest.requester?.deleted_at ?? null) != null ? (
                        <Link
                          to={`/users/${groupRequest.requester?.id}`}
                          sx={{textDecoration: 'line-through', color: 'inherit'}}
                          component={RouterLink}>
                          <b>{groupRequest.requester?.email.toLowerCase()}</b>
                        </Link>
                      ) : (
                        <Link
                          to={`/users/${groupRequest.requester?.email.toLowerCase()}`}
                          sx={{textDecoration: 'none', color: 'inherit'}}
                          component={RouterLink}>
                          <b>{groupRequest.requester?.email.toLowerCase()}</b>
                        </Link>
                      )}
                      {groupRequest.status == 'PENDING' ? ' is requesting ' : ' requested '}
                      {'the creation of group '}
                      {groupRequest.status == 'APPROVED' ? (
                        <Link
                          to={`/groups/${requestedGroupName}`}
                          sx={{textDecoration: 'none', color: 'inherit', fontWeight: 500}}
                          component={RouterLink}>
                          <b>{requestedGroupName}</b>
                        </Link>
                      ) : (
                        <b>{requestedGroupName}</b>
                      )}
                    </Typography>
                    <Typography variant="body2" sx={{pt: 1}}>
                      <b>Reason:</b> {groupRequest.request_reason ? groupRequest.request_reason : 'No reason given'}
                    </Typography>
                  </Paper>
                  <Paper sx={{p: 2, mt: 1, mb: 0}}>
                    <Typography variant="h6" gutterBottom>
                      Requested Details
                    </Typography>
                    <Grid container spacing={1}>
                      <Grid item xs={12}>
                        <Typography variant="body2">
                          <b>Name:</b> {groupRequest.requested_group_name ?? '—'}
                        </Typography>
                      </Grid>
                      <Grid item xs={12}>
                        <Typography variant="body2">
                          <b>Type:</b> {GROUP_TYPE_ID_TO_LABELS[requestedGroupType] ?? requestedGroupType}
                        </Typography>
                      </Grid>
                      {requestedAppData && (
                        <Grid item xs={12} sm={6}>
                          <Typography variant="body2">
                            <b>App:</b> {requestedAppData.name}
                          </Typography>
                        </Grid>
                      )}
                      <Grid item xs={12}>
                        <Typography variant="body2">
                          <b>Description:</b> {groupRequest.requested_group_description ?? '—'}
                        </Typography>
                      </Grid>
                      {groupRequest.requested_ownership_ending_at && (
                        <Grid item xs={12} sm={6}>
                          <Typography variant="body2">
                            <b>Ownership Ending:</b>{' '}
                            {dayjs(groupRequest.requested_ownership_ending_at).startOf('second').fromNow()}
                          </Typography>
                        </Grid>
                      )}
                      {requestedTagNames.length > 0 && (
                        <Grid item xs={12}>
                          <Stack direction="row" spacing={1} flexWrap="wrap" alignItems="center">
                            <Typography variant="body2">
                              <b>Tags:</b>
                            </Typography>
                            {requestedTags.map((tag) => (
                              <Chip key={tag.id} label={tag.name} size="small" />
                            ))}
                          </Stack>
                        </Grid>
                      )}
                    </Grid>
                  </Paper>
                </TimelineContent>
              </TimelineItem>
              <TimelineItem>
                <TimelineOppositeContent sx={{m: 'auto 0'}} align="right">
                  {groupRequest.resolved_at != null ? (
                    <span title={groupRequest.resolved_at}>
                      {dayjs(groupRequest.resolved_at).startOf('second').fromNow()}
                    </span>
                  ) : null}
                </TimelineOppositeContent>
                <TimelineSeparator>
                  <TimelineConnector />
                  {groupRequest.status == 'PENDING' ? (
                    <TimelineDot color="primary">
                      <PendingIcon />
                    </TimelineDot>
                  ) : groupRequest.status == 'APPROVED' ? (
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
                  {groupRequest.status == 'PENDING' ? (
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
                                Request is <b>pending</b>. Review and adjust the group details below, then approve or
                                reject.
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
                                <Typography variant="h6" sx={{mb: 1}}>
                                  Group Details
                                </Typography>
                                <Grid container spacing={2} alignItems="center">
                                  <Grid item xs={6}>
                                    <FormControl size="small" margin="normal" fullWidth>
                                      <SelectElement
                                        label="Type"
                                        name="resolved_group_type"
                                        options={GROUP_TYPE_OPTIONS}
                                        onChange={(value) => {
                                          setGroupType(value);
                                          setAppName('');
                                        }}
                                        required
                                        disabled={!admin}
                                      />
                                    </FormControl>
                                  </Grid>
                                  {groupType == 'app_group' ? (
                                    <Grid item xs={6}>
                                      <FormControl margin="normal" fullWidth required>
                                        <AutocompleteElement
                                          label="App"
                                          name="resolved_app"
                                          options={appSearchOptions}
                                          required
                                          autocompleteProps={{
                                            getOptionLabel: (option: App) => option.name,
                                            isOptionEqualToValue: (option: App, value: App) => option.id == value.id,
                                            onInputChange: (_event: React.SyntheticEvent, newInputValue: string) =>
                                              setAppSearchInput(newInputValue),
                                            onChange: (_event: React.SyntheticEvent, value: App | null) =>
                                              setAppName(value?.name ?? ''),
                                            disabled: !admin,
                                          }}
                                        />
                                      </FormControl>
                                    </Grid>
                                  ) : null}
                                </Grid>
                                <FormControl margin="normal" fullWidth>
                                  <Box sx={{display: 'flex', flexDirection: 'row', alignItems: 'center'}}>
                                    {groupType == 'app_group' || groupType == 'role_group' ? (
                                      <Box sx={{mx: 1}}>
                                        <Typography noWrap variant="h6">
                                          {groupType == 'role_group'
                                            ? ROLE_GROUP_PREFIX
                                            : APP_GROUP_PREFIX +
                                              (appName == '' ? '<App>' : appName) +
                                              APP_NAME_APP_GROUP_SEPARATOR}
                                        </Typography>
                                      </Box>
                                    ) : null}
                                    <TextFieldElement
                                      fullWidth
                                      label="Name"
                                      name="resolved_group_name"
                                      variant="outlined"
                                      validation={{
                                        maxLength: 255,
                                        pattern: new RegExp(accessConfig.NAME_VALIDATION_PATTERN),
                                      }}
                                      parseError={(error) => {
                                        if (error?.message != '') return error?.message ?? '';
                                        if (error.type == 'maxLength')
                                          return 'Name can be at most 255 characters in length';
                                        if (error.type == 'pattern')
                                          return (
                                            accessConfig.NAME_VALIDATION_ERROR +
                                            ' Regex to match: ' +
                                            accessConfig.NAME_VALIDATION_PATTERN
                                          );
                                        return '';
                                      }}
                                      required
                                    />
                                  </Box>
                                </FormControl>
                                <FormControl margin="normal" fullWidth>
                                  <TextFieldElement
                                    label="Description"
                                    name="resolved_group_description"
                                    multiline
                                    rows={4}
                                    validation={{maxLength: 1024}}
                                    parseError={(error) => {
                                      if (error?.message != '') return error?.message ?? '';
                                      if (error.type == 'maxLength')
                                        return 'Description can be at most 1024 characters in length';
                                      return '';
                                    }}
                                  />
                                </FormControl>
                                <Grid container columnSpacing={2} rowSpacing={0} alignItems="center">
                                  {ownershipTimeLimit != null && (
                                    <Grid item xs={12}>
                                      <Typography variant="subtitle2" color="text.accent" sx={{pt: 1}}>
                                        {'Ownership is limited to ' +
                                          Math.floor(ownershipTimeLimit / 86400) +
                                          ' days by a tag constraint.'}
                                      </Typography>
                                    </Grid>
                                  )}
                                  <Grid item xs={6}>
                                    <OwnershipEndingField
                                      ownershipTimeLimit={ownershipTimeLimit}
                                      ownershipUntil={ownershipUntil}
                                      setOwnershipUntil={setOwnershipUntil}
                                    />
                                  </Grid>
                                  <Grid item xs={6}>
                                    <FormControl margin="normal" fullWidth>
                                      <Autocomplete
                                        multiple
                                        options={tagSearchOptions}
                                        value={selectedTags}
                                        getOptionLabel={(option: Tag) => option.name}
                                        onInputChange={(_event, newInputValue) => setTagSearchInput(newInputValue)}
                                        onChange={(_event, newValue) => setSelectedTags(newValue)}
                                        renderTags={(value: Tag[], getTagProps) =>
                                          value.map((option: Tag, index: number) => (
                                            <Chip variant="outlined" label={option.name} {...getTagProps({index})} />
                                          ))
                                        }
                                        renderInput={(params) => (
                                          <TextField {...params} label="Tags" placeholder="Tags" />
                                        )}
                                      />
                                    </FormControl>
                                  </Grid>
                                </Grid>
                                <Grid>
                                  {ownershipUntil == 'custom' ? (
                                    <Grid item xs={6} sx={{pr: 1}}>
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
                                    </Grid>
                                  ) : null}
                                </Grid>
                                <Divider sx={{my: 2}} />
                              </>
                            )}

                            <FormControl margin="normal" fullWidth>
                              <TextFieldElement
                                label={
                                  ownRequest
                                    ? 'Why? (provide a reason)'
                                    : 'Why is this group creation approved or rejected? (provide a reason)'
                                }
                                name="resolution_reason"
                                multiline
                                rows={4}
                                validation={{maxLength: 1024}}
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
                                  disabled={submitting}
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
                                {'Reject'}
                              </Button>
                              {submitting ? <CircularProgress sx={{mx: 2}} size={40} /> : null}
                            </FormControl>
                          </FormContainer>
                        </Paper>
                      </Box>
                    ) : (
                      <Paper sx={{p: 2, my: 2}}>
                        <Typography variant="body1">
                          Request is <b>pending</b> and can be reviewed by app owners or Access admins.
                        </Typography>
                      </Paper>
                    )
                  ) : (
                    <Paper sx={{p: 2, my: 2}}>
                      <Typography variant="body1">
                        {groupRequest.resolver == null ? (
                          <b>Access</b>
                        ) : (
                          <Link
                            to={`/users/${groupRequest.resolver?.email.toLowerCase()}`}
                            sx={{textDecoration: 'none', color: 'inherit'}}
                            component={RouterLink}>
                            <b>{groupRequest.resolver?.email.toLowerCase()}</b>
                          </Link>
                        )}
                        {groupRequest.status == 'APPROVED' ? (
                          <>
                            {' approved the request and created group '}
                            <Link
                              to={`/groups/${groupRequest.resolved_group_name ?? requestedGroupName}`}
                              sx={{textDecoration: 'none', color: 'inherit'}}
                              component={RouterLink}>
                              <b>{groupRequest.resolved_group_name ?? requestedGroupName}</b>
                            </Link>
                            {'.'}
                          </>
                        ) : (
                          ' rejected the request.'
                        )}
                      </Typography>
                      <Typography variant="body2" sx={{py: 1}}>
                        <b>Reason:</b>{' '}
                        {groupRequest.resolution_reason ? groupRequest.resolution_reason : 'No reason given'}
                      </Typography>
                      {groupRequest.status === 'APPROVED' && approvedDetails.length > 0 && (
                        <Box sx={{mt: 1}}>
                          <Typography variant="body1" sx={{mb: 0.5}}>
                            <b>Approved Details:</b>
                          </Typography>
                          <Grid container spacing={0.5} sx={{pl: 2}}>
                            {approvedDetails.map((field) => {
                              if (field.kind === 'tags') {
                                if (field.resolvedTags.length === 0 && field.requestedTagNames.length === 0) {
                                  return null;
                                }
                                return (
                                  <Grid item xs={12} key="tags">
                                    <Stack direction="row" spacing={1} flexWrap="wrap" alignItems="center">
                                      <Typography variant="body2">
                                        <b>Tags:</b>
                                      </Typography>
                                      {field.changed && (
                                        <>
                                          {requestedTags.map((tag) => (
                                            <Chip
                                              key={tag.id}
                                              label={tag.name}
                                              size="small"
                                              sx={{textDecoration: 'line-through', opacity: 0.6}}
                                            />
                                          ))}
                                          <Typography variant="body2">{'→'}</Typography>
                                        </>
                                      )}
                                      {field.resolvedTags.length > 0 ? (
                                        field.resolvedTags.map((tag) => (
                                          <Chip key={tag.id} label={tag.name} size="small" />
                                        ))
                                      ) : (
                                        <Typography variant="body2">None</Typography>
                                      )}
                                    </Stack>
                                  </Grid>
                                );
                              }
                              return (
                                <Grid item xs={12} key={field.label}>
                                  <Typography variant="body2">
                                    <b>{field.label}:</b>{' '}
                                    {field.changed ? (
                                      <>
                                        <span style={{textDecoration: 'line-through'}}>{field.from}</span>
                                        {' → '}
                                        {field.value}
                                      </>
                                    ) : (
                                      field.value
                                    )}
                                  </Typography>
                                </Grid>
                              );
                            })}
                          </Grid>
                        </Box>
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
