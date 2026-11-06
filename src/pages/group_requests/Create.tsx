import * as React from 'react';
import dayjs, {Dayjs} from 'dayjs';
import IsSameOrBefore from 'dayjs/plugin/isSameOrBefore';
import {Link as RouterLink, useNavigate} from 'react-router-dom';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import CircularProgress from '@mui/material/CircularProgress';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import FormControl from '@mui/material/FormControl';
import Grid from '@mui/material/Grid';
import Link from '@mui/material/Link';
import GroupRequestIcon from '@mui/icons-material/GroupAdd';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';

import {FormContainer, AutocompleteElement, SelectElement, TextFieldElement} from 'react-hook-form-mui';
import {DatePickerElement} from 'react-hook-form-mui/date-pickers';

import {
  useGroupRequestsCreate,
  useApps,
  useGroups,
  useAppById,
  useTags,
  GroupRequestsCreateError,
  GroupRequestsCreateVariables,
} from '../../api/apiComponents';
import {AccessLengthField, endingAt} from '../../components/AccessLength';
import AppGroupLifecyclePluginConfigurationForm from '../../components/AppGroupLifecyclePluginConfigurationForm';
import {pluginIdForApp, extractRequestedPluginData} from './pluginConfig';
import TagsField, {inheritedAppTags} from '../../components/TagsField';
import {PrincipalsField, ROLES_IN_ROLE_ERROR, RoleOption} from '../../components/Principals';
import {
  AppDetail,
  AppGroupDetail,
  AppSummary,
  OktaUserDetail,
  OktaUserSummary,
  GroupDetail,
  GroupRequestDetail,
  GroupSummary,
  TagListItem,
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

const APP_GROUP_PREFIX = 'App-';
const APP_NAME_APP_GROUP_SEPARATOR = '-';
const ROLE_GROUP_PREFIX = 'Role-';

/**
 * The full name a request would ask for: the typed name with the prefix its type
 * implies. An okta_group name typed as "App-<app>-…" takes the matched app's
 * canonical spelling, since such a request is filed as an app group. One typed as
 * "Role-…" is filed as a role group and is already the full role name, so it is
 * returned as typed.
 */
function requestedGroupName(
  type: CreateGroupRequestForm['type'],
  name: string,
  selectedApp: AppDetail | null,
  detectedApp: AppSummary | null,
): string {
  if (type === 'okta_group' && name.startsWith(APP_GROUP_PREFIX) && detectedApp != null) {
    const withoutPrefix = name.slice(APP_GROUP_PREFIX.length);
    const sepIdx = withoutPrefix.indexOf(APP_NAME_APP_GROUP_SEPARATOR);
    const rest = withoutPrefix.slice(sepIdx + APP_NAME_APP_GROUP_SEPARATOR.length);
    return APP_GROUP_PREFIX + detectedApp.name + APP_NAME_APP_GROUP_SEPARATOR + rest;
  }
  if (type === 'app_group') {
    return APP_GROUP_PREFIX + (selectedApp?.name ?? '') + APP_NAME_APP_GROUP_SEPARATOR + name;
  }
  if (type === 'role_group') {
    return ROLE_GROUP_PREFIX + name;
  }
  return name;
}

interface CreateGroupRequestForm {
  type: 'okta_group' | 'app_group' | 'role_group';
  app?: AppDetail;
  name: string;
  description?: string;
  ownershipUntil?: string;
  customOwnershipUntil?: string;
  membershipUntil?: string;
  customMembershipUntil?: string;
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
  const [selectedTags, setSelectedTags] = React.useState<Array<TagListItem>>([]);
  const [ownershipUntil, setOwnershipUntil] = React.useState(accessConfig.DEFAULT_ACCESS_TIME);
  const [membershipUntil, setMembershipUntil] = React.useState(accessConfig.DEFAULT_ACCESS_TIME);
  const [ownerUsers, setOwnerUsers] = React.useState<OktaUserSummary[]>([]);
  const [ownerRoles, setOwnerRoles] = React.useState<RoleOption[]>([]);
  const [memberUsers, setMemberUsers] = React.useState<OktaUserSummary[]>([]);
  const [memberRoles, setMemberRoles] = React.useState<RoleOption[]>([]);
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

  // `effectiveAppId` already covers both ways a request lands under an app, so a
  // name typed with an "App-...-" prefix inherits that app's tags too.
  const inheritedTags = React.useMemo(
    () =>
      inheritedAppTags(
        effectiveAppDetail,
        selectedTags.map((tag) => tag.id),
      ),
    [effectiveAppDetail, selectedTags],
  );

  // The group does not exist yet, so its constraints come from the tags it
  // would be created with rather than from an id.
  const constraints = useConstraintsForTags([
    ...selectedTags.map((tag) => tag.id),
    ...(inheritedTags?.tags ?? []).map((tag) => tag.id),
  ]);
  const ownershipTimeLimit = constraints.timeLimit(true);
  const membershipTimeLimit = constraints.timeLimit(false);

  const noOwners = ownerUsers.length === 0 && ownerRoles.length === 0;
  const namesMembers = memberUsers.length > 0 || memberRoles.length > 0;
  // A plain group named "Role-..." is filed as a role, so it cannot take roles either.
  const requestsRole =
    groupType === 'role_group' || (groupType === 'okta_group' && nameInput.startsWith(ROLE_GROUP_PREFIX));
  const rolesBlocked = requestsRole && (ownerRoles.length > 0 || memberRoles.length > 0);

  // Look up the requested name so a taken one is flagged before submitting.
  // The search matches substrings of names and descriptions, so compare the
  // results exactly; names are unique ignoring case.
  const candidateName =
    nameInput === '' || (groupType === 'app_group' && selectedApp == null)
      ? ''
      : requestedGroupName(groupType, nameInput, selectedApp, detectedApp);
  const {data: sameNameGroupData} = useGroups(
    {queryParams: {page: 1, size: 50, q: candidateName}},
    {enabled: candidateName !== ''},
  );
  const nameTaken =
    candidateName !== '' &&
    (sameNameGroupData?.items ?? []).some(
      (group: GroupSummary) => group.name.toLowerCase() === candidateName.toLowerCase(),
    );

  const {data: tagSearchData} = useTags({
    queryParams: {page: 1, size: 10, q: tagSearchInput},
  });
  const tagSearchOptions = tagSearchData?.items ?? [];

  const submit = (formData: CreateGroupRequestForm) => {
    setSubmitting(true);

    let effectiveType = formData.type;
    let appId: string | undefined = undefined;

    if (rolesBlocked) {
      setSubmitting(false);
      setRequestError(ROLES_IN_ROLE_ERROR);
      return;
    }

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
    } else if (formData.type === 'okta_group' && formData.name.startsWith(ROLE_GROUP_PREFIX)) {
      effectiveType = 'role_group';
    } else if (formData.type === 'app_group') {
      appId = selectedApp?.id;
    }
    const groupName = requestedGroupName(formData.type, formData.name, selectedApp, detectedApp);

    const requestedPluginData = extractRequestedPluginData(formData as any);

    const body = {
      requested_group_name: groupName,
      requested_group_description: formData.description ?? '',
      requested_group_type: effectiveType,
      requested_app_id: appId,
      request_reason: formData.reason ?? '',
      requested_group_tags: selectedTags.map((t) => t.id),
      requested_owner_user_ids: ownerUsers.map((u) => u.id),
      requested_owner_role_ids: ownerRoles.map((r) => r.id),
      requested_member_user_ids: memberUsers.map((u) => u.id),
      requested_member_role_ids: memberRoles.map((r) => r.id),
      ...(effectiveType === 'app_group' && requestPluginId ? {requested_plugin_data: requestedPluginData} : {}),
    } as Parameters<typeof createRequest.mutate>[0]['body'];

    if (body == null) {
      setSubmitting(false);
      return;
    }

    body.requested_ownership_ending_at = endingAt(formData.ownershipUntil, formData.customOwnershipUntil);
    if (namesMembers) {
      body.requested_membership_ending_at = endingAt(formData.membershipUntil, formData.customMembershipUntil);
    }

    createRequest.mutate({body});
  };

  return (
    <FormContainer<CreateGroupRequestForm>
      defaultValues={{type: 'okta_group', ownershipUntil: '1209600', membershipUntil: '1209600'}}
      onSuccess={(formData) => submit(formData)}>
      <DialogTitle> Create Group Request</DialogTitle>
      <DialogContent>
        {requestError !== '' && <Alert severity="error">{requestError}</Alert>}
        <Typography
          variant="subtitle1"
          sx={{
            color: 'text.accent',
          }}>
          {noOwners
            ? 'If this request is approved, you will be added as the group owner.'
            : 'If this request is approved, the owners named below will be added as the group owners.'}
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
              // Show the app the form holds. rhf-mui otherwise displays the entry of `options`
              // matching the form value, and `options` is a search this field's input drives: an
              // in-flight search empties it, so the selection blanks and the input resets.
              transform={{input: (app) => app ?? null}}
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
          {nameTaken && (
            <Alert severity="error" sx={{mt: 1}}>
              A group named "{candidateName}" already exists. To join it,{' '}
              <Link component={RouterLink} to="/requests" color="inherit" onClick={() => props.setOpen(false)}>
                request access to it
              </Link>{' '}
              instead.
            </Alert>
          )}
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
          <TagsField
            options={tagSearchOptions}
            selected={selectedTags}
            inherited={inheritedTags}
            onSelectedChange={setSelectedTags}
            onSearchChange={setTagSearchInput}
          />
        </FormControl>
        <PrincipalsField
          label="Owners"
          users={ownerUsers}
          roles={ownerRoles}
          onUsersChange={setOwnerUsers}
          onRolesChange={setOwnerRoles}
          rolesDisallowed={requestsRole}
          helperText="Leave empty to make yourself the owner."
        />
        <FormControl margin="normal" fullWidth>
          {ownershipTimeLimit != null && (
            <Typography
              variant="subtitle2"
              sx={{
                color: 'text.accent',
                marginBottom: '12px',
              }}>
              {'Ownership is limited to ' + timeLimitLabel(ownershipTimeLimit) + ' by a tag constraint.'}
            </Typography>
          )}
          <Grid
            container
            spacing={2}
            sx={{
              alignItems: 'flex-start',
            }}>
            <Grid size={6}>
              <AccessLengthField
                name="ownershipUntil"
                label="Requested ownership length"
                timeLimit={ownershipTimeLimit}
                blocked={constraints.blocked}
                onChange={setOwnershipUntil}
              />
            </Grid>
            <Grid size={6}>
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
        <PrincipalsField
          label="Members"
          users={memberUsers}
          roles={memberRoles}
          onUsersChange={setMemberUsers}
          onRolesChange={setMemberRoles}
          rolesDisallowed={requestsRole}
        />
        {namesMembers && (
          <FormControl margin="normal" fullWidth>
            {membershipTimeLimit != null && (
              <Typography
                variant="subtitle2"
                sx={{
                  color: 'text.accent',
                  marginBottom: '12px',
                }}>
                {'Membership is limited to ' + timeLimitLabel(membershipTimeLimit) + ' by a tag constraint.'}
              </Typography>
            )}
            <Grid
              container
              spacing={2}
              sx={{
                alignItems: 'flex-start',
              }}>
              <Grid size={6}>
                <AccessLengthField
                  name="membershipUntil"
                  label="Requested membership length"
                  timeLimit={membershipTimeLimit}
                  blocked={constraints.blocked}
                  onChange={setMembershipUntil}
                />
              </Grid>
              <Grid size={6}>
                {membershipUntil === 'custom' && (
                  <DatePickerElement
                    label="Custom Membership End Date"
                    name="customMembershipUntil"
                    shouldDisableDate={(date: Dayjs) => date.isSameOrBefore(dayjs(), 'day')}
                    sx={{width: '100%'}}
                    required
                  />
                )}
              </Grid>
            </Grid>
          </FormControl>
        )}
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
        <Button type="submit" disabled={submitting || nameTaken || rolesBlocked}>
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
