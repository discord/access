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
import IconButton from '@mui/material/IconButton';
import Link from '@mui/material/Link';
import Paper from '@mui/material/Paper';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import AddIcon from '@mui/icons-material/Add';
import AppRequestIcon from '@mui/icons-material/AddBox';
import RemoveIcon from '@mui/icons-material/Delete';

import {FormContainer, TextFieldElement} from 'react-hook-form-mui';
import {DatePickerElement} from 'react-hook-form-mui/date-pickers';

import {
  useAppRequestsCreate,
  useApps,
  useTags,
  AppRequestsCreateError,
  AppRequestsCreateVariables,
} from '../../api/apiComponents';
import {
  AppRequestDetail,
  AppSummary,
  CreateAppRequestBody,
  OktaUserDetail,
  OktaUserSummary,
  TagListItem,
} from '../../api/apiSchemas';
import {timeLimitLabel, useConstraintsForTags} from '../../constraints';
import accessConfig, {requireDescriptions} from '../../config/accessConfig';
import TagsField from '../group_requests/TagsField';
import {PrincipalsField, RoleOption} from '../group_requests/Principals';
import {AccessLengthField, endingAt} from '../group_requests/Create';

dayjs.extend(IsSameOrBefore);

const APP_GROUP_PREFIX = 'App-';
const APP_NAME_APP_GROUP_SEPARATOR = '-';
const APP_OWNERS_GROUP_NAME_SUFFIX = 'Owners';

/** The prefix every app group of the named app carries. */
export function appGroupPrefix(appName: string): string {
  return APP_GROUP_PREFIX + appName + APP_NAME_APP_GROUP_SEPARATOR;
}

/**
 * The problem with each initial app group's name, or null for a name that is fine.
 *
 * Args:
 *   appName: The requested app's name, which prefixes every group name.
 *   suffixes: The names typed for the groups, without the prefix.
 *
 * Returns:
 *   One entry per suffix. Names are compared ignoring case, as the API compares them.
 */
export function appGroupNameErrors(appName: string, suffixes: string[]): Array<string | null> {
  const counts = new Map<string, number>();
  for (const suffix of suffixes) {
    const key = suffix.trim().toLowerCase();
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const pattern = new RegExp(accessConfig.NAME_VALIDATION_PATTERN);
  return suffixes.map((suffix) => {
    const trimmed = suffix.trim();
    if (trimmed === '') {
      return 'Name is required';
    }
    if (trimmed.toLowerCase() === APP_OWNERS_GROUP_NAME_SUFFIX.toLowerCase()) {
      return `The ${appGroupPrefix(appName)}${APP_OWNERS_GROUP_NAME_SUFFIX} group is created with the app. Name the app owners above instead.`;
    }
    if ((counts.get(trimmed.toLowerCase()) ?? 0) > 1) {
      return 'Another group has this name';
    }
    if (trimmed.length > 255) {
      return 'Name can be at most 255 characters';
    }
    if (appName !== '' && !pattern.test(appGroupPrefix(appName) + trimmed)) {
      return accessConfig.NAME_VALIDATION_ERROR + ' Regex: ' + accessConfig.NAME_VALIDATION_PATTERN;
    }
    return null;
  });
}

interface AppGroupRow {
  key: number;
  suffix: string;
  description: string;
  ownerUsers: OktaUserSummary[];
  ownerRoles: RoleOption[];
  memberUsers: OktaUserSummary[];
  memberRoles: RoleOption[];
}

function emptyRow(key: number): AppGroupRow {
  return {key, suffix: '', description: '', ownerUsers: [], ownerRoles: [], memberUsers: [], memberRoles: []};
}

interface AppGroupFieldsProps {
  row: AppGroupRow;
  prefix: string;
  nameError: string | null;
  onChange: (row: AppGroupRow) => void;
  onRemove: () => void;
}

function AppGroupFields({row, prefix, nameError, onChange, onRemove}: AppGroupFieldsProps) {
  return (
    <Paper variant="outlined" sx={{p: 2, mt: 1}} data-testid="app-group-row">
      <Box sx={{display: 'flex', flexDirection: 'row', alignItems: 'flex-start'}}>
        <Box sx={{mx: 1, pt: 1.5}}>
          <Typography noWrap variant="h6">
            {prefix}
          </Typography>
        </Box>
        <TextField
          fullWidth
          label="Group name"
          value={row.suffix}
          onChange={(e) => onChange({...row, suffix: e.target.value})}
          error={nameError != null}
          helperText={nameError ?? undefined}
          required
        />
        <Tooltip title="Remove this group">
          <IconButton aria-label="Remove app group" onClick={onRemove} sx={{ml: 1, mt: 1}}>
            <RemoveIcon />
          </IconButton>
        </Tooltip>
      </Box>
      <FormControl margin="normal" fullWidth>
        <TextField
          label="Group description"
          value={row.description}
          onChange={(e) => onChange({...row, description: e.target.value})}
          multiline
          rows={2}
          slotProps={{htmlInput: {maxLength: 1024}}}
        />
      </FormControl>
      <PrincipalsField
        label="Owners"
        users={row.ownerUsers}
        roles={row.ownerRoles}
        onUsersChange={(ownerUsers) => onChange({...row, ownerUsers})}
        onRolesChange={(ownerRoles) => onChange({...row, ownerRoles})}
        rolesDisallowed={false}
      />
      <PrincipalsField
        label="Members"
        users={row.memberUsers}
        roles={row.memberRoles}
        onUsersChange={(memberUsers) => onChange({...row, memberUsers})}
        onRolesChange={(memberRoles) => onChange({...row, memberRoles})}
        rolesDisallowed={false}
      />
    </Paper>
  );
}

interface CreateAppRequestForm {
  name: string;
  description?: string;
  ownershipUntil?: string;
  customOwnershipUntil?: string;
  membershipUntil?: string;
  customMembershipUntil?: string;
  reason?: string;
}

interface CreateRequestContainerProps {
  currentUser: OktaUserDetail;
  setOpen(open: boolean): void;
}

function CreateRequestContainer(props: CreateRequestContainerProps) {
  const navigate = useNavigate();

  const [nameInput, setNameInput] = React.useState('');
  const [tagSearchInput, setTagSearchInput] = React.useState('');
  const [selectedTags, setSelectedTags] = React.useState<Array<TagListItem>>([]);
  const [ownershipUntil, setOwnershipUntil] = React.useState(accessConfig.DEFAULT_ACCESS_TIME);
  const [membershipUntil, setMembershipUntil] = React.useState(accessConfig.DEFAULT_ACCESS_TIME);
  const [ownerUsers, setOwnerUsers] = React.useState<OktaUserSummary[]>([]);
  const [ownerRoles, setOwnerRoles] = React.useState<RoleOption[]>([]);
  const [groups, setGroups] = React.useState<AppGroupRow[]>([]);
  const nextKey = React.useRef(0);
  const [submitAttempted, setSubmitAttempted] = React.useState(false);
  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);

  const complete = (
    completedRequest: AppRequestDetail | undefined,
    error: AppRequestsCreateError | null,
    variables: AppRequestsCreateVariables,
    context: any,
  ) => {
    setSubmitting(false);
    if (error != null) {
      setRequestError(error.payload.toString());
    } else {
      props.setOpen(false);
      navigate('/app-requests/' + encodeURIComponent(completedRequest?.id ?? ''));
    }
  };

  const createRequest = useAppRequestsCreate({onSettled: complete});

  const {data: tagSearchData} = useTags({
    queryParams: {page: 1, size: 10, q: tagSearchInput},
  });
  const tagSearchOptions = tagSearchData?.items ?? [];

  // The search matches substrings, so compare the results exactly; app names are unique ignoring case.
  const candidateName = nameInput.trim();
  const {data: sameNameAppData} = useApps(
    {queryParams: {page: 1, size: 50, q: candidateName}},
    {enabled: candidateName !== ''},
  );
  const sameNameApp =
    candidateName === ''
      ? undefined
      : (sameNameAppData?.items ?? []).find(
          (app: AppSummary) => app.name.toLowerCase() === candidateName.toLowerCase(),
        );
  const nameTaken = sameNameApp != null;

  // The app's tags apply to its owners group and every group created under it.
  const constraints = useConstraintsForTags(selectedTags.map((tag) => tag.id));
  const ownershipTimeLimit = constraints.timeLimit(true);
  const membershipTimeLimit = constraints.timeLimit(false);

  const noOwners = ownerUsers.length === 0 && ownerRoles.length === 0;
  const namesMembers = groups.some((group) => group.memberUsers.length > 0 || group.memberRoles.length > 0);

  const prefix = appGroupPrefix(candidateName === '' ? '<App>' : candidateName);
  const groupNameErrors = appGroupNameErrors(
    candidateName,
    groups.map((group) => group.suffix),
  );
  const groupsInvalid = groupNameErrors.some((error) => error != null);

  const updateGroup = (index: number, row: AppGroupRow) =>
    setGroups((current) => current.map((group, i) => (i === index ? row : group)));
  const removeGroup = (index: number) => setGroups((current) => current.filter((_group, i) => i !== index));
  const addGroup = () => setGroups((current) => [...current, emptyRow(nextKey.current++)]);

  const submit = (formData: CreateAppRequestForm) => {
    if (groupsInvalid) {
      return;
    }
    setSubmitting(true);

    const appName = formData.name.trim();
    const body: CreateAppRequestBody = {
      requested_app_name: appName,
      requested_app_description: formData.description ?? '',
      requested_app_tags: selectedTags.map((tag) => tag.id),
      requested_owner_user_ids: ownerUsers.map((user) => user.id),
      requested_owner_role_ids: ownerRoles.map((role) => role.id),
      requested_app_groups: groups.map((group) => ({
        name: appGroupPrefix(appName) + group.suffix.trim(),
        description: group.description,
        owner_user_ids: group.ownerUsers.map((user) => user.id),
        owner_role_ids: group.ownerRoles.map((role) => role.id),
        member_user_ids: group.memberUsers.map((user) => user.id),
        member_role_ids: group.memberRoles.map((role) => role.id),
      })),
      requested_ownership_ending_at: endingAt(formData.ownershipUntil, formData.customOwnershipUntil),
      request_reason: formData.reason ?? '',
    };
    if (namesMembers) {
      body.requested_membership_ending_at = endingAt(formData.membershipUntil, formData.customMembershipUntil);
    }

    createRequest.mutate({body});
  };

  return (
    <FormContainer<CreateAppRequestForm>
      defaultValues={{ownershipUntil: '1209600', membershipUntil: '1209600'}}
      onSuccess={(formData) => submit(formData)}>
      <DialogTitle>Request an App</DialogTitle>
      <DialogContent>
        {requestError !== '' && <Alert severity="error">{requestError}</Alert>}
        <Typography
          variant="subtitle1"
          sx={{
            color: 'text.accent',
          }}>
          {noOwners
            ? 'If this request is approved, you will be added as the app owner.'
            : 'If this request is approved, the owners named below will be added as the app owners.'}
        </Typography>
        <FormControl margin="normal" fullWidth>
          <Box onChange={(e: React.ChangeEvent<HTMLInputElement>) => setNameInput(e.target.value)}>
            <TextFieldElement
              fullWidth
              label="App name"
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
          {sameNameApp != null && (
            <Alert severity="error" sx={{mt: 1}}>
              An app named "{sameNameApp.name}" already exists. To get access to it,{' '}
              <Link
                component={RouterLink}
                to={`/apps/${sameNameApp.name}`}
                color="inherit"
                onClick={() => props.setOpen(false)}>
                request access to its groups
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
            inherited={null}
            onSelectedChange={setSelectedTags}
            onSearchChange={setTagSearchInput}
          />
        </FormControl>
        <PrincipalsField
          label="App owners"
          users={ownerUsers}
          roles={ownerRoles}
          onUsersChange={setOwnerUsers}
          onRolesChange={setOwnerRoles}
          rolesDisallowed={false}
          helperText="Leave empty to make yourself the owner. App owners own and are members of the app's owners group."
        />
        <FormControl margin="normal" fullWidth>
          <Typography variant="h6">Initial app groups</Typography>
          <Typography variant="body2" sx={{color: 'text.secondary'}}>
            Groups to create with the app, each with its own owners and members. More can be added after approval.
          </Typography>
          {groups.map((group, index) => (
            <AppGroupFields
              key={group.key}
              row={group}
              prefix={prefix}
              nameError={
                groupNameErrors[index] != null && (group.suffix.trim() !== '' || submitAttempted)
                  ? groupNameErrors[index]
                  : null
              }
              onChange={(row) => updateGroup(index, row)}
              onRemove={() => removeGroup(index)}
            />
          ))}
          <Box sx={{mt: 1}}>
            <Button startIcon={<AddIcon />} onClick={addGroup}>
              Add app group
            </Button>
          </Box>
        </FormControl>
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
            label="Why do you need this app?"
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
      </DialogContent>
      <DialogActions>
        <Button onClick={() => props.setOpen(false)}>Cancel</Button>
        <Button
          type="submit"
          disabled={submitting || nameTaken || (submitAttempted && groupsInvalid)}
          onClick={() => setSubmitAttempted(true)}>
          {submitting ? <CircularProgress size={24} /> : 'Submit'}
        </Button>
      </DialogActions>
    </FormContainer>
  );
}

interface CreateRequestProps {
  currentUser: OktaUserDetail;
  open?: boolean;
  setOpen?: (open: boolean) => void;
}

/** The "Request an App" button and the dialog it opens, or just the dialog when the caller controls `open`. */
export default function CreateAppRequest(props: CreateRequestProps) {
  const [internalOpen, setInternalOpen] = React.useState(false);
  const open = props.open ?? internalOpen;
  const setOpen = props.setOpen ?? setInternalOpen;

  return (
    <>
      {props.setOpen == null && (
        <Tooltip title="Request that a new app be created, with its owners and initial groups.">
          <span>
            <Button variant="contained" onClick={() => setOpen(true)} endIcon={<AppRequestIcon />}>
              Request an App
            </Button>
          </span>
        </Tooltip>
      )}
      {open && (
        <Dialog open onClose={() => setOpen(false)} maxWidth="md" fullWidth>
          <CreateRequestContainer currentUser={props.currentUser} setOpen={setOpen} />
        </Dialog>
      )}
    </>
  );
}
