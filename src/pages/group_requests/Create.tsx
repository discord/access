import * as React from 'react';
import dayjs, {Dayjs} from 'dayjs';
import IsSameOrBefore from 'dayjs/plugin/isSameOrBefore';
import {useNavigate} from 'react-router-dom';
import Alert from '@mui/material/Alert';
import Autocomplete from '@mui/material/Autocomplete';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import CircularProgress from '@mui/material/CircularProgress';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import FormControl from '@mui/material/FormControl';
import Grid from '@mui/material/Grid';
import GroupRequestIcon from '@mui/icons-material/GroupAdd';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';

import {FormContainer, AutocompleteElement, SelectElement, TextFieldElement} from 'react-hook-form-mui';
import {DatePickerElement} from 'react-hook-form-mui/date-pickers';
import {useFormContext, useWatch} from 'react-hook-form';

import {
  useGroupRequestsCreate,
  useApps,
  useAppById,
  useTags,
  GroupRequestsCreateError,
  GroupRequestsCreateVariables,
} from '../../api/apiComponents';
import AppGroupLifecyclePluginConfigurationForm from '../../components/AppGroupLifecyclePluginConfigurationForm';
import {pluginIdForApp, extractRequestedPluginData} from './pluginConfig';
import {
  AppDetail,
  AppGroupDetail,
  AppSummary,
  AppTagMapDetail,
  OktaUserDetail,
  GroupDetail,
  GroupRequestDetail,
  TagDetail,
  TagSummary,
} from '../../api/apiSchemas';
import {timeLimitLabel, useConstraintsForTags} from '../../constraints';
import {isAccessAdmin, isAppOwnerGroupOwner} from '../../authorization';
import accessConfig, {requireDescriptions} from '../../config/accessConfig';

dayjs.extend(IsSameOrBefore);

const GROUP_TYPE_ID_TO_LABELS: Record<string, string> = {
  okta_group: 'Group',
  app_group: 'App Group',
  role_group: 'Role',
} as const;

const GROUP_TYPE_OPTIONS = Object.entries(GROUP_TYPE_ID_TO_LABELS).map(([id, label]) => ({id, label}));

// Offers only the durations the tags in force allow, and moves the field off
// one they do not. A request the constraints forbid is not refused on submit --
// `ApproveGroupRequest` shortens it on approval instead -- so a duration left
// on offer here is one the requester is told they asked for and does not get.
function OwnershipLengthField({
  timeLimit,
  blocked,
  onChange,
}: {
  timeLimit: number | null;
  blocked: boolean;
  onChange: (value: string) => void;
}) {
  const {control, setValue} = useFormContext();
  const selected = useWatch({control, name: 'ownershipUntil'});

  const [options, defaultId] = React.useMemo<[Array<{id: string; label: string}>, string]>(() => {
    // While the answer is unknown the limit reads as null. Holding the narrower
    // list rather than re-offering the full one keeps a duration the tags forbid
    // from being briefly clickable.
    if (timeLimit == null) {
      return blocked ? [[], accessConfig.DEFAULT_ACCESS_TIME] : [UNTIL_OPTIONS, accessConfig.DEFAULT_ACCESS_TIME];
    }
    const [lastId, filtered] = filterUntilLabels(timeLimit);
    return [filtered, lastId];
  }, [timeLimit, blocked]);

  React.useEffect(() => {
    if (timeLimit == null) return;
    if (!options.some((option) => option.id === selected)) {
      setValue('ownershipUntil', defaultId);
      onChange(defaultId);
    }
  }, [timeLimit, options, defaultId, selected, setValue, onChange]);

  return (
    <SelectElement
      fullWidth
      label="Requested ownership length"
      name="ownershipUntil"
      options={options}
      onChange={(value) => onChange(value)}
      required
    />
  );
}

const APP_GROUP_PREFIX = 'App-';
const APP_NAME_APP_GROUP_SEPARATOR = '-';
const ROLE_GROUP_PREFIX = 'Role-';

const UNTIL_ID_TO_LABELS: Record<string, string> = {
  '43200': '12 Hours',
  '432000': '5 Days',
  '1209600': 'Two Weeks',
  '2592000': '30 Days',
  '7776000': '90 Days',
  indefinite: 'Indefinite',
  custom: 'Custom',
} as const;

const UNTIL_OPTIONS = Object.entries(UNTIL_ID_TO_LABELS).map(([id, label]) => ({id, label}));

const UNTIL_NUMERIC_ID_TO_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries(UNTIL_ID_TO_LABELS).filter(([key]) => !isNaN(Number(key))),
);

// The durations still on offer under `timeLimit`, and the longest of them.
// Indefinite is not among them, and neither is any duration over the limit;
// Custom stays, since its own picker is bounded separately.
function filterUntilLabels(timeLimit: number): [string, Array<{id: string; label: string}>] {
  const withinLimit = Object.entries(UNTIL_NUMERIC_ID_TO_LABELS).filter(([key]) => Number(key) <= timeLimit);
  const labels = [...withinLimit, ['custom', 'Custom']].map(([id, label]) => ({id, label}));
  return [withinLimit.at(-1)?.[0] ?? 'custom', labels];
}

interface CreateGroupRequestForm {
  type: 'okta_group' | 'app_group' | 'role_group';
  app?: AppDetail;
  name: string;
  description?: string;
  ownershipUntil?: string;
  customOwnershipUntil?: string;
  reason?: string;
}

interface CreateRequestButtonProps {
  setOpen(open: boolean): void;
}

function CreateRequestButton(props: CreateRequestButtonProps) {
  return (
    <Tooltip title="Request that a new group or role be created.">
      <span>
        <Button variant="contained" onClick={() => props.setOpen(true)} endIcon={<GroupRequestIcon />}>
          Create Request
        </Button>
      </span>
    </Tooltip>
  );
}

interface CreateRequestContainerProps {
  currentUser: OktaUserDetail;
  setOpen(open: boolean): void;
}

function CreateRequestContainer(props: CreateRequestContainerProps) {
  const navigate = useNavigate();

  const [groupType, setGroupType] = React.useState<'okta_group' | 'app_group' | 'role_group'>('okta_group');
  const [selectedApp, setSelectedApp] = React.useState<AppDetail | null>(null);
  const [appSearchInput, setAppSearchInput] = React.useState('');
  const [tagSearchInput, setTagSearchInput] = React.useState('');
  const [nameInput, setNameInput] = React.useState('');
  const [selectedTags, setSelectedTags] = React.useState<Array<TagDetail>>([]);
  const [ownershipUntil, setOwnershipUntil] = React.useState(accessConfig.DEFAULT_ACCESS_TIME);
  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);

  const complete = (
    completedRequest: GroupRequestDetail | undefined,
    error: GroupRequestsCreateError | null,
    variables: GroupRequestsCreateVariables,
    context: any,
  ) => {
    setSubmitting(false);
    if (error != null) {
      setRequestError(error.payload.toString());
    } else {
      props.setOpen(false);
      navigate('/group-requests/' + encodeURIComponent(completedRequest?.id ?? ''));
    }
  };

  const createRequest = useGroupRequestsCreate({onSettled: complete});

  const {data: appSearchData} = useApps({
    queryParams: {page: 1, size: 10, q: appSearchInput},
  });
  const appSearchOptions = appSearchData?.items ?? [];

  const detectedAppName = React.useMemo(() => {
    if (groupType !== 'okta_group' || !nameInput.startsWith(APP_GROUP_PREFIX)) return '';
    const withoutPrefix = nameInput.slice(APP_GROUP_PREFIX.length);
    const sepIdx = withoutPrefix.indexOf(APP_NAME_APP_GROUP_SEPARATOR);
    if (sepIdx <= 0) return '';
    return withoutPrefix.slice(0, sepIdx);
  }, [groupType, nameInput]);

  const {data: detectedAppData} = useApps({
    queryParams: {page: 1, size: 10, q: detectedAppName},
  });
  const detectedApp = React.useMemo(
    () => detectedAppData?.items?.find((app: AppSummary) => app.name === detectedAppName) ?? null,
    [detectedAppName, detectedAppData],
  );

  // The app a submitted request would target: explicitly-selected app, or the
  // one detected from an "App-…-" name prefix on an okta_group request.
  const effectiveAppId = (groupType === 'app_group' ? selectedApp?.id : detectedApp?.id) ?? null;
  // AppSummary (from search) lacks app_group_lifecycle_plugin; fetch AppDetail for it.
  const {data: effectiveAppDetail} = useAppById(
    {pathParams: {appId: effectiveAppId ?? ''}},
    {enabled: effectiveAppId != null},
  );
  const requestPluginId = pluginIdForApp(effectiveAppDetail);

  // The tags `CreateGroup` will copy from the app onto the group, on top of the
  // ones chosen here. `effectiveAppId` already covers both ways a request lands
  // under an app, so a name typed with an "App-...-" prefix inherits them too.
  // Tags chosen directly are left out: the Tags field is showing them, and the
  // constraint reader deduplicates.
  const inheritedAppTags = React.useMemo<TagSummary[]>(() => {
    const chosen = new Set(selectedTags.map((tag) => tag.id));
    const appTagMaps: AppTagMapDetail[] = effectiveAppDetail?.active_app_tags ?? [];
    return appTagMaps
      .map((mapping) => mapping.active_tag)
      .filter((tag): tag is TagSummary => tag != null && !chosen.has(tag.id));
  }, [effectiveAppDetail, selectedTags]);

  // The group does not exist yet, so its constraints come from the tags it
  // would be created with rather than from an id.
  const constraints = useConstraintsForTags([
    ...selectedTags.map((tag) => tag.id),
    ...inheritedAppTags.map((tag) => tag.id),
  ]);
  const ownershipTimeLimit = constraints.timeLimit(true);

  const {data: tagSearchData} = useTags({
    queryParams: {page: 1, size: 10, q: tagSearchInput},
  });
  const tagSearchOptions = tagSearchData?.items ?? [];

  const submit = (formData: CreateGroupRequestForm) => {
    setSubmitting(true);

    let effectiveType = formData.type;
    let groupName = formData.name;
    let appId: string | undefined = undefined;

    if (formData.type === 'okta_group' && formData.name.startsWith(APP_GROUP_PREFIX) && detectedAppName === '') {
      setSubmitting(false);
      setRequestError(
        `Requested name starts with the app group prefix "${APP_GROUP_PREFIX}" but does not list an app. App group names should be in the format "${APP_GROUP_PREFIX}<app name>${APP_NAME_APP_GROUP_SEPARATOR}<group name>".`,
      );
      return;
    } else if (formData.type === 'okta_group' && formData.name.startsWith(APP_GROUP_PREFIX) && detectedApp == null) {
      setSubmitting(false);
      setRequestError(
        `Requested name starts with the app group prefix "${APP_GROUP_PREFIX}" but no app named "${detectedAppName}" was found.`,
      );
      return;
    } else if (formData.type === 'okta_group' && formData.name.startsWith(APP_GROUP_PREFIX) && detectedApp != null) {
      effectiveType = 'app_group';
      appId = detectedApp.id;
      const withoutPrefix = formData.name.slice(APP_GROUP_PREFIX.length);
      const sepIdx = withoutPrefix.indexOf(APP_NAME_APP_GROUP_SEPARATOR);
      const rest = withoutPrefix.slice(sepIdx + APP_NAME_APP_GROUP_SEPARATOR.length);
      groupName = APP_GROUP_PREFIX + detectedApp.name + APP_NAME_APP_GROUP_SEPARATOR + rest;
    } else if (formData.type === 'okta_group' && formData.name.startsWith(ROLE_GROUP_PREFIX)) {
      effectiveType = 'role_group';
      groupName = ROLE_GROUP_PREFIX + formData.name.slice(ROLE_GROUP_PREFIX.length);
    } else if (formData.type === 'app_group') {
      groupName = APP_GROUP_PREFIX + (selectedApp?.name ?? '') + APP_NAME_APP_GROUP_SEPARATOR + formData.name;
      appId = selectedApp?.id;
    } else if (formData.type === 'role_group') {
      groupName = ROLE_GROUP_PREFIX + formData.name;
    }

    const requestedPluginData = extractRequestedPluginData(formData as any);

    const body = {
      requested_group_name: groupName,
      requested_group_description: formData.description ?? '',
      requested_group_type: effectiveType,
      requested_app_id: appId,
      request_reason: formData.reason ?? '',
      requested_group_tags: selectedTags.map((t) => t.id),
      ...(effectiveType === 'app_group' && requestPluginId ? {requested_plugin_data: requestedPluginData} : {}),
    } as Parameters<typeof createRequest.mutate>[0]['body'];

    if (body == null) {
      setSubmitting(false);
      return;
    }

    switch (formData.ownershipUntil) {
      case 'indefinite':
        break;
      case 'custom':
        body.requested_ownership_ending_at = (formData.customOwnershipUntil as unknown as Dayjs).toISOString();
        break;
      default:
        body.requested_ownership_ending_at = dayjs()
          .add(parseInt(formData.ownershipUntil ?? '0', 10), 'seconds')
          .toISOString();
        break;
    }

    createRequest.mutate({body});
  };

  return (
    <FormContainer<CreateGroupRequestForm>
      defaultValues={{type: 'okta_group', ownershipUntil: '1209600'}}
      onSuccess={(formData) => submit(formData)}>
      <DialogTitle> Create Group Request</DialogTitle>
      <DialogContent>
        {requestError !== '' && <Alert severity="error">{requestError}</Alert>}
        <Typography variant="subtitle1" color="text.accent">
          If this request is approved, you will be added as the group owner.
        </Typography>
        <FormControl size="small" margin="normal" fullWidth>
          <SelectElement
            label="Group Type"
            name="type"
            options={GROUP_TYPE_OPTIONS}
            onChange={(value) => {
              setGroupType(value);
            }}
            required
          />
        </FormControl>
        {groupType === 'app_group' && (
          <FormControl margin="normal" fullWidth required>
            <AutocompleteElement<(typeof appSearchOptions)[number]>
              label="App"
              name="app"
              options={appSearchOptions}
              required
              autocompleteProps={{
                getOptionLabel: (option) => option.name,
                isOptionEqualToValue: (option, value) => option.id === value?.id,
                onInputChange: (_event, newVal) => setAppSearchInput(newVal),
                onChange: (_event, value) => setSelectedApp(value ?? null),
              }}
            />
          </FormControl>
        )}
        <FormControl margin="normal" fullWidth>
          <Box
            sx={{
              display: 'flex',
              justifyContent: 'flex-start',
              flexDirection: 'row',
              alignItems: 'center',
            }}>
            {groupType == 'app_group' || groupType == 'role_group' ? (
              <Box sx={{mx: 1}}>
                <Typography noWrap={true} variant="h6">
                  {groupType == 'role_group'
                    ? ROLE_GROUP_PREFIX
                    : APP_GROUP_PREFIX +
                      (selectedApp?.name == null ? '<AppDetail>' : selectedApp.name) +
                      APP_NAME_APP_GROUP_SEPARATOR}
                </Typography>
              </Box>
            ) : null}
            <Box onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNameInput(e.target.value)} sx={{flexGrow: 1}}>
              <TextFieldElement
                fullWidth
                label="Name"
                name="name"
                variant="outlined"
                rules={{
                  maxLength: 255,
                  pattern: new RegExp(accessConfig.NAME_VALIDATION_PATTERN),
                }}
                parseError={(error) => {
                  if (error?.message) return error.message;
                  if (error?.type === 'maxLength') return 'Name can be at most 255 characters';
                  if (error?.type === 'pattern')
                    return accessConfig.NAME_VALIDATION_ERROR + ' Regex: ' + accessConfig.NAME_VALIDATION_PATTERN;
                  return '';
                }}
                required
              />
            </Box>
          </Box>
        </FormControl>
        <FormControl margin="normal" fullWidth>
          <TextFieldElement
            label="Description"
            name="description"
            multiline
            rows={3}
            rules={{maxLength: 1024}}
            parseError={(error) => {
              if (error?.message) return error.message;
              if (error?.type === 'maxLength') return 'Description can be at most 1024 characters';
              return '';
            }}
            required={requireDescriptions}
          />
        </FormControl>
        <FormControl margin="normal" fullWidth>
          <Autocomplete
            multiple
            options={tagSearchOptions}
            value={selectedTags}
            getOptionLabel={(option) => option.name}
            onInputChange={(_event, newVal) => setTagSearchInput(newVal)}
            onChange={(_event, newVal) => setSelectedTags(newVal)}
            renderTags={(value: TagDetail[], getTagProps) =>
              value.map((option: TagDetail, index: number) => (
                <Chip variant="outlined" label={option.name} {...getTagProps({index})} />
              ))
            }
            renderInput={(params) => <TextField {...params} label="Tags" placeholder="Tags" />}
          />
          {/* Shown outside the Tags field because these are not the requester's
              to choose or remove: the group picks them up from its app. */}
          {inheritedAppTags.length > 0 && (
            <Box sx={{marginTop: '8px'}}>
              <Typography variant="caption" color="text.secondary">
                Also inherited from {effectiveAppDetail?.name}:
              </Typography>
              <Box sx={{display: 'flex', flexWrap: 'wrap', gap: '4px', marginTop: '4px'}}>
                {inheritedAppTags.map((tag) => (
                  <Chip key={tag.id} size="small" variant="outlined" label={tag.name} />
                ))}
              </Box>
            </Box>
          )}
        </FormControl>
        <FormControl margin="normal" fullWidth>
          {ownershipTimeLimit != null && (
            <Typography variant="subtitle2" color="text.accent" sx={{marginBottom: '12px'}}>
              {'Ownership is limited to ' + timeLimitLabel(ownershipTimeLimit) + ' by a tag constraint.'}
            </Typography>
          )}
          <Grid container alignItems="flex-start" spacing={2}>
            <Grid item xs={6}>
              <OwnershipLengthField
                timeLimit={ownershipTimeLimit}
                blocked={constraints.blocked}
                onChange={setOwnershipUntil}
              />
            </Grid>
            <Grid item xs={6}>
              {ownershipUntil === 'custom' && (
                <DatePickerElement
                  label="Custom End Date"
                  name="customOwnershipUntil"
                  shouldDisableDate={(date: Dayjs) => date.isSameOrBefore(dayjs(), 'day')}
                  sx={{width: '100%'}}
                  required
                />
              )}
            </Grid>
          </Grid>
        </FormControl>
        <FormControl margin="normal" fullWidth>
          <TextFieldElement
            label="Why do you need this group?"
            name="reason"
            multiline
            rows={4}
            rules={{maxLength: 1024}}
            parseError={(error) => {
              if (error?.message) return error.message;
              if (error?.type === 'maxLength') return 'Reason can be at most 1024 characters';
              return '';
            }}
            required
          />
        </FormControl>
        {requestPluginId && (
          <AppGroupLifecyclePluginConfigurationForm entityType="group" selectedPluginId={requestPluginId} />
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={() => props.setOpen(false)}>Cancel</Button>
        <Button type="submit" disabled={submitting}>
          {submitting ? <CircularProgress size={24} /> : 'Submit'}
        </Button>
      </DialogActions>
    </FormContainer>
  );
}

interface CreateRequestDialogProps {
  currentUser: OktaUserDetail;
  setOpen(open: boolean): void;
}

function CreateRequestDialog(props: CreateRequestDialogProps) {
  return (
    <Dialog open onClose={() => props.setOpen(false)} maxWidth="sm" fullWidth>
      <CreateRequestContainer {...props} />
    </Dialog>
  );
}

interface CreateRequestProps {
  currentUser: OktaUserDetail;
  open?: boolean;
  setOpen?: (open: boolean) => void;
}

export default function CreateRequest(props: CreateRequestProps) {
  const [internalOpen, setInternalOpen] = React.useState(false);
  const open = props.open ?? internalOpen;
  const setOpen = props.setOpen ?? setInternalOpen;

  return (
    <>
      {props.setOpen == null && <CreateRequestButton setOpen={setOpen} />}
      {open && <CreateRequestDialog currentUser={props.currentUser} setOpen={setOpen} />}
    </>
  );
}
