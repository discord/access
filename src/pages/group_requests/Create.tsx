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

import {
  FormContainer,
  AutocompleteElement,
  SelectElement,
  DatePickerElement,
  TextFieldElement,
} from 'react-hook-form-mui';

import {
  useCreateGroupRequest,
  useGetApps,
  useGetTags,
  CreateGroupRequestError,
  CreateGroupRequestVariables,
} from '../../api/apiComponents';
import {App, AppGroup, OktaUser, PolymorphicGroup, GroupRequest, Tag} from '../../api/apiSchemas';
import {isAccessAdmin, isAppOwnerGroupOwner} from '../../authorization';
import accessConfig, {requireDescriptions} from '../../config/accessConfig';

dayjs.extend(IsSameOrBefore);

const GROUP_TYPE_ID_TO_LABELS: Record<string, string> = {
  okta_group: 'Group',
  app_group: 'App Group',
  role_group: 'Role',
} as const;

const GROUP_TYPE_OPTIONS = Object.entries(GROUP_TYPE_ID_TO_LABELS).map(([id, label]) => ({id, label}));

const APP_GROUP_PREFIX = 'App-';
const APP_NAME_APP_GROUP_SEPARATOR = '-';
const ROLE_GROUP_PREFIX = 'Role-';

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

const UNTIL_OPTIONS = Object.entries(UNTIL_ID_TO_LABELS).map(([id, label]) => ({id, label}));

interface CreateGroupRequestForm {
  type: 'okta_group' | 'app_group' | 'role_group';
  app?: App;
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
  currentUser: OktaUser;
  setOpen(open: boolean): void;
}

function CreateRequestContainer(props: CreateRequestContainerProps) {
  const navigate = useNavigate();

  const [groupType, setGroupType] = React.useState<'okta_group' | 'app_group' | 'role_group'>('okta_group');
  const [selectedApp, setSelectedApp] = React.useState<App | null>(null);
  const [appSearchInput, setAppSearchInput] = React.useState('');
  const [tagSearchInput, setTagSearchInput] = React.useState('');
  const [nameInput, setNameInput] = React.useState('');
  const [selectedTags, setSelectedTags] = React.useState<Array<Tag>>([]);
  const [ownershipUntil, setOwnershipUntil] = React.useState(accessConfig.DEFAULT_ACCESS_TIME);
  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);

  const complete = (
    completedRequest: GroupRequest | undefined,
    error: CreateGroupRequestError | null,
    variables: CreateGroupRequestVariables,
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

  const createRequest = useCreateGroupRequest({onSettled: complete});

  const {data: appSearchData} = useGetApps({
    queryParams: {page: 0, per_page: 10, q: appSearchInput},
  });
  const appSearchOptions = appSearchData?.results ?? [];

  const detectedAppName = React.useMemo(() => {
    if (groupType !== 'okta_group' || !nameInput.startsWith(APP_GROUP_PREFIX)) return '';
    const withoutPrefix = nameInput.slice(APP_GROUP_PREFIX.length);
    const sepIdx = withoutPrefix.indexOf(APP_NAME_APP_GROUP_SEPARATOR);
    if (sepIdx <= 0) return '';
    return withoutPrefix.slice(0, sepIdx);
  }, [groupType, nameInput]);

  const {data: detectedAppData} = useGetApps({
    queryParams: {page: 0, per_page: 10, q: detectedAppName},
  });
  const detectedApp = React.useMemo(
    () => detectedAppData?.results?.find((app) => app.name === detectedAppName) ?? null,
    [detectedAppName, detectedAppData],
  );

  const {data: tagSearchData} = useGetTags({
    queryParams: {page: 0, per_page: 10, q: tagSearchInput},
  });
  const tagSearchOptions = tagSearchData?.results ?? [];

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

    const body = {
      requested_group_name: groupName,
      requested_group_description: formData.description ?? '',
      requested_group_type: effectiveType,
      requested_app_id: appId,
      request_reason: formData.reason ?? '',
      requested_group_tags: selectedTags.map((t) => t.id),
    } as Parameters<typeof createRequest.mutate>[0]['body'];

    switch (formData.ownershipUntil) {
      case 'indefinite':
        break;
      case 'custom':
        body.requested_ownership_ending_at = (formData.customOwnershipUntil as unknown as Dayjs).format(RFC822_FORMAT);
        break;
      default:
        body.requested_ownership_ending_at = dayjs()
          .add(parseInt(formData.ownershipUntil ?? '0', 10), 'seconds')
          .format(RFC822_FORMAT);
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
            <AutocompleteElement
              label="App"
              name="app"
              options={appSearchOptions}
              required
              autocompleteProps={{
                getOptionLabel: (option) => option.name,
                isOptionEqualToValue: (option, value) => option.id === value.id,
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
                      (selectedApp?.name == null ? '<App>' : selectedApp.name) +
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
                validation={{
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
            validation={{maxLength: 1024}}
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
            renderTags={(value: Tag[], getTagProps) =>
              value.map((option: Tag, index: number) => (
                <Chip variant="outlined" label={option.name} {...getTagProps({index})} />
              ))
            }
            renderInput={(params) => <TextField {...params} label="Tags" placeholder="Tags" />}
          />
        </FormControl>
        <FormControl margin="normal" fullWidth>
          <Grid container alignItems="center" spacing={2}>
            <Grid item xs={6}>
              <SelectElement
                fullWidth
                label="Requested ownership length"
                name="ownershipUntil"
                options={UNTIL_OPTIONS}
                onChange={(value) => setOwnershipUntil(value)}
                required
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
            validation={{maxLength: 1024}}
            parseError={(error) => {
              if (error?.message) return error.message;
              if (error?.type === 'maxLength') return 'Reason can be at most 1024 characters';
              return '';
            }}
            required
          />
        </FormControl>
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
  currentUser: OktaUser;
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
  currentUser: OktaUser;
}

export default function CreateRequest(props: CreateRequestProps) {
  const [open, setOpen] = React.useState(false);

  return (
    <>
      <CreateRequestButton setOpen={setOpen} />
      {open && <CreateRequestDialog currentUser={props.currentUser} setOpen={setOpen} />}
    </>
  );
}
