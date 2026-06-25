import * as React from 'react';
import dayjs, {Dayjs} from 'dayjs';
import IsSameOrBefore from 'dayjs/plugin/isSameOrBefore';
import {useNavigate} from 'react-router-dom';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import GroupAddIcon from '@mui/icons-material/GroupAdd';
import Alert from '@mui/material/Alert';
import FormControl from '@mui/material/FormControl';
import Grid from '@mui/material/Grid';
import Typography from '@mui/material/Typography';
import Box from '@mui/material/Box';
import Divider from '@mui/material/Divider';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemText from '@mui/material/ListItemText';
import IconButton from '@mui/material/IconButton';
import InputLabel from '@mui/material/InputLabel';
import DeleteIcon from '@mui/icons-material/Close';
import CircularProgress from '@mui/material/CircularProgress';
import Select from '@mui/material/Select';
import MenuItem from '@mui/material/MenuItem';

import {
  FormContainer,
  SelectElement,
  AutocompleteElement,
  DatePickerElement,
  TextFieldElement,
} from 'react-hook-form-mui';

import {
  useGroups,
  useRoleMembersByIdPut,
  RoleMembersByIdPutError,
  RoleMembersByIdPutVariables,
} from '../../api/apiComponents';
import {
  GroupDetail,
  GroupSummary,
  AppGroupDetail,
  OktaGroupDetail,
  OktaUserGroupMemberDetail,
  RoleMember,
  RoleMembersSummary,
  OktaUserDetail,
} from '../../api/apiSchemas';
import {isAccessAdmin, isGroupOwner} from '../../authorization';
import {minTagTimeGroups, requiredReasonGroups, ownerCantAddSelfGroups} from '../../helpers';
import {useCurrentUser} from '../../authentication';
import {group} from 'console';
import accessConfig from '../../config/accessConfig';

dayjs.extend(IsSameOrBefore);

interface AddGroupsButtonProps {
  owner: boolean;
  setOpen(open: boolean): any;
}

function AddGroupsButton(props: AddGroupsButtonProps) {
  return (
    <Button variant="contained" onClick={() => props.setOpen(true)} endIcon={<GroupAddIcon />}>
      {'Add ' + (props.owner ? 'Owner Groups' : 'Groups')}
    </Button>
  );
}

interface AddGroupsDialogProps {
  currentUser: OktaUserDetail;
  owner: boolean;
  group: GroupDetail;
  setOpen(open: boolean): any;
}

interface AddGroupsForm {
  customUntil?: string;
  reason?: string;
}

const GROUP_TYPE_ID_TO_LABELS: Record<string, string> = {
  okta_group: 'Group',
  app_group: 'App Group',
  role_group: 'Role',
} as const;

const UNTIL_ID_TO_LABELS: Record<string, string> = accessConfig.ACCESS_TIME_LABELS;
const UNTIL_JUST_NUMERIC_ID_TO_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries(UNTIL_ID_TO_LABELS).filter(([key]) => !isNaN(Number(key))),
);
const UNTIL_OPTIONS = Object.entries(UNTIL_ID_TO_LABELS).map(([id, label], index) => ({id: id, label: label}));

function AddGroupsDialog(props: AddGroupsDialogProps) {
  const navigate = useNavigate();
  const currentUser = useCurrentUser();

  const currUserRoleGroupMember = (currentUser.active_group_memberships ?? []).some(
    (membership) => membership.active_group?.id === props.group.id,
  );

  const userOwnedNonRoleGroupIds = !isAccessAdmin(currentUser)
    ? (currentUser.active_group_ownerships ?? [])
        .filter(
          (ownership: OktaUserGroupMemberDetail) =>
            ownership.active_group != null && ownership.active_group.type !== 'role_group',
        )
        .map((ownership: OktaUserGroupMemberDetail) => ownership.active_group!.id!)
    : null;

  const [until, setUntil] = React.useState(accessConfig.DEFAULT_ACCESS_TIME);
  const [groupSearchInput, setGroupSearchInput] = React.useState('');
  const [groups, setGroups] = React.useState<Array<OktaGroupDetail | AppGroupDetail>>([]);
  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);
  const [labels, setLabels] = React.useState<Array<Record<string, string>>>(UNTIL_OPTIONS);
  const [timeLimit, setTimeLimit] = React.useState<number | null>(null);
  const [requiredReason, setRequiredReason] = React.useState<boolean>(false);

  const complete = (
    completedUsersChange: RoleMembersSummary | undefined,
    error: RoleMembersByIdPutError | null,
    variables: RoleMembersByIdPutVariables,
    context: any,
  ) => {
    setSubmitting(false);
    if (error != null) {
      setRequestError(error.payload.toString());
    } else {
      props.setOpen(false);
      navigate(0);
    }
  };

  const putGroupUsers = useRoleMembersByIdPut({
    onSettled: complete,
  });

  const {data: groupSearchData} = useGroups({
    queryParams: {
      page: 1,
      size: 10,
      managed: true,
      q: groupSearchInput,
    },
  });
  const groupSearchOptions = groupSearchData?.items ?? [];

  // Preload the non-admin's owned groups so they appear the moment the dialog opens, before any
  // typing — otherwise the empty search returns the first page of all managed groups (none owned),
  // and the filter below correctly drops them, leaving a confusing "No options". Shares its query
  // key with the gate's managed-groups fetch, so it's already cached (no extra request). Admins
  // (userOwnedNonRoleGroupIds == null) can add any group, so they keep the search-driven options.
  const {data: managedGroupsData} = useGroups(
    {queryParams: {managed: true, page: 1, size: 1000}},
    {enabled: userOwnedNonRoleGroupIds != null},
  );
  const ownedGroupOptions = (managedGroupsData?.items ?? []).filter(
    (group: GroupSummary) => userOwnedNonRoleGroupIds != null && userOwnedNonRoleGroupIds.includes(group.id),
  );
  // With no search text, a non-admin sees their owned groups; once they type, defer to the search.
  const autocompleteOptions =
    userOwnedNonRoleGroupIds != null && groupSearchInput.trim() === '' ? ownedGroupOptions : groupSearchOptions;

  const updateUntil = (time: number | null) => {
    setTimeLimit(time);
    if (!(time == null)) {
      const filteredUntil = Object.keys(UNTIL_JUST_NUMERIC_ID_TO_LABELS)
        .filter((key) => Number(key) <= time!)
        .reduce(
          (obj, key) => {
            obj[key] = UNTIL_JUST_NUMERIC_ID_TO_LABELS[key];
            return obj;
          },
          {} as Record<string, string>,
        );

      setUntil(Object.keys(filteredUntil).at(-1)!);

      setLabels(
        Object.entries(Object.assign({}, filteredUntil, {custom: 'Custom'})).map(([id, label], index) => ({
          id: id,
          label: label,
        })),
      );
    } else {
      setLabels(UNTIL_OPTIONS);
    }
  };

  const submit = (groupsForm: AddGroupsForm) => {
    setSubmitting(true);

    const roleMembers: RoleMember = {
      groups_to_add: [],
      groups_to_remove: [],
      owner_groups_to_add: [],
      owner_groups_to_remove: [],
    };

    if (props.owner) {
      roleMembers.owner_groups_to_add = groups.map((group) => group?.id ?? '');
    } else {
      roleMembers.groups_to_add = groups.map((group) => group?.id ?? '');
    }

    switch (until) {
      case 'indefinite':
        break;
      case 'custom':
        roleMembers.groups_added_ending_at = (groupsForm.customUntil as unknown as Dayjs).toISOString();
        break;
      default:
        roleMembers.groups_added_ending_at = dayjs()
          .add(parseInt(until ?? '0', 10), 'seconds')
          .toISOString();
        break;
    }

    roleMembers.created_reason = groupsForm.reason ?? '';

    putGroupUsers.mutate({
      body: roleMembers,
      pathParams: {roleId: props.group?.id ?? ''},
    });
  };

  const removeGroupFromList = (groupId: string) => {
    setGroups(groups.filter((group) => group.id != groupId));
  };

  const addGroupsText = props.owner ? 'Owner Groups' : 'Member Groups';

  return (
    <Dialog open fullWidth onClose={() => props.setOpen(false)}>
      <FormContainer<AddGroupsForm> onSuccess={(formData) => submit(formData)}>
        <DialogTitle>Add {addGroupsText}</DialogTitle>
        <DialogContent>
          {!isAccessAdmin(currentUser) ? (
            <Alert severity="info" sx={{my: 1}}>
              You can only add groups that you own that do not have certain tag constraints from this dialog. To add
              this role to groups you do not own or that have tags preventing you from directly adding the role to the
              group, please create a role request.
            </Alert>
          ) : null}
          <Typography variant="subtitle1" color="text.accent">
            {timeLimit
              ? (props.owner ? 'Ownership of ' : 'Membership to ') +
                'one or more selected groups is limited to ' +
                Math.floor(timeLimit / 86400) +
                ' days.'
              : null}
          </Typography>
          {requestError != '' ? <Alert severity="error">{requestError}</Alert> : null}
          <FormControl size="small" margin="normal" fullWidth>
            <InputLabel>For how long?</InputLabel>
            <Select
              label="For how long?"
              name="until"
              value={until}
              onChange={(event) => setUntil(event.target.value)}
              required>
              {labels.map((label) => {
                return (
                  <MenuItem key={label.id} value={label.id}>
                    {label.label}
                  </MenuItem>
                );
              })}
            </Select>
          </FormControl>
          {until == 'custom' ? (
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
          <FormControl fullWidth sx={{margin: '2px 0'}}>
            <TextFieldElement
              label="Why? (provide a reason)"
              name="reason"
              multiline
              rows={4}
              required={requiredReason}
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
          <FormControl fullWidth sx={{margin: '8px 0'}}>
            <AutocompleteElement
              label={'Search for ' + addGroupsText + ' to Add'}
              name="group"
              options={autocompleteOptions}
              autocompleteProps={{
                getOptionLabel: (option) => option.name,
                isOptionEqualToValue: (option, value) => option.id == value.id,
                filterOptions: (options) =>
                  options.filter(
                    (option) =>
                      // A role cannot contain another role.
                      option.type != 'role_group' &&
                      // Externally managed groups have their membership managed outside Access,
                      // so the role can't be added to them — true even for admins.
                      option.is_managed == true &&
                      // Non-admins may only add the role to non-role groups they own; admins (null) to any.
                      (userOwnedNonRoleGroupIds == null || userOwnedNonRoleGroupIds.includes(option.id!)) &&
                      // Don't offer groups already staged for addition.
                      !groups.map((group) => group.id).includes(option.id) &&
                      // Enforce the self-add tag constraint: a non-admin who is a member of the role
                      // can't add it to a group whose tags disallow self-add for this dimension
                      // (membership or ownership, per props.owner). Checked against the option's own
                      // tags, since the current user's ownership refs carry no tag data. Admins are exempt.
                      !(
                        currUserRoleGroupMember &&
                        !isAccessAdmin(currentUser) &&
                        ownerCantAddSelfGroups([option], props.owner)
                      ),
                  ),
                onInputChange: (event, newInputValue, reason) => {
                  if (reason != 'reset') {
                    setGroupSearchInput(newInputValue);
                  }
                },
                onChange: (event, value) => {
                  if (value != null) {
                    const allGroups = [value, ...groups];
                    setGroups(allGroups);
                    setGroupSearchInput('');
                    updateUntil(minTagTimeGroups(allGroups, props.owner));
                    setRequiredReason(requiredReasonGroups(allGroups, props.owner));
                  } else {
                    updateUntil(null);
                    setRequiredReason(false);
                  }
                },
                inputValue: groupSearchInput,
                renderOption: (props, option, state) => {
                  return (
                    <li {...props}>
                      <Grid container alignItems="center">
                        <Grid item>
                          <Box>{option.name}</Box>
                          <Typography variant="body2" color="text.secondary">
                            {GROUP_TYPE_ID_TO_LABELS[option.type]}
                          </Typography>
                        </Grid>
                      </Grid>
                    </li>
                  );
                },
              }}
            />
          </FormControl>
          <FormControl fullWidth sx={{marginTop: '5px'}}>
            <InputLabel shrink={groups.length > 0}>{addGroupsText} to Add</InputLabel>
            <List
              sx={{
                overflow: 'auto',
                minHeight: 300,
                maxHeight: 600,
                backgroundColor: (theme) =>
                  theme.palette.mode === 'light' ? theme.palette.grey[100] : theme.palette.grey[900],
              }}
              dense={true}>
              {groups.map((group) => (
                <React.Fragment key={group.id}>
                  <ListItem
                    sx={{py: 0}}
                    secondaryAction={
                      <IconButton edge="end" aria-label="delete" onClick={() => removeGroupFromList(group?.id ?? '')}>
                        <DeleteIcon />
                      </IconButton>
                    }>
                    <ListItemText
                      primary={group?.name ?? ''}
                      secondary={GROUP_TYPE_ID_TO_LABELS[group?.type ?? 'okta_group']}
                    />
                  </ListItem>
                  <Divider />
                </React.Fragment>
              ))}
            </List>
          </FormControl>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => props.setOpen(false)}>Cancel</Button>
          <Button type="submit" disabled={submitting || groups.length == 0}>
            {submitting ? <CircularProgress size={24} /> : 'Add'}
          </Button>
        </DialogActions>
      </FormContainer>
    </Dialog>
  );
}

interface AddGroupsProps {
  currentUser: OktaUserDetail;
  group: GroupDetail;
  owner?: boolean;
}

export default function AddGroups(props: AddGroupsProps) {
  const [open, setOpen] = React.useState(false);
  const owner = props.owner ?? false;

  const isAdmin = isAccessAdmin(props.currentUser);
  const isRoleOwner = isGroupOwner(props.currentUser, props.group.id ?? '');
  const isRoleMember = (props.currentUser.active_group_memberships ?? []).some(
    (membership) => membership.active_group?.id === props.group.id,
  );

  // The managed, non-role groups this user owns — the only groups a non-admin can add the role to.
  // type/is_managed are available on the ownership refs in the current-user payload (tags are not).
  const ownedTargetGroups = (props.currentUser.active_group_ownerships ?? [])
    .map((ownership) => ownership.active_group)
    .filter((group) => group != null && group.type !== 'role_group' && group.is_managed === true);

  // The self-add tag constraint only bites when the owner is also a member of the role (adding the
  // role then adds themselves) and isn't an admin (admins are exempt). Only then must we know each
  // owned group's tags — which the current-user payload omits — so fetch the managed groups (those
  // summaries carry tags) to check whether any owned group is still addable. Otherwise skip the call.
  const needsConstraintCheck = !isAdmin && isRoleOwner && isRoleMember && ownedTargetGroups.length > 0;
  const {data: managedGroupsData, isLoading: managedGroupsLoading} = useGroups(
    {queryParams: {managed: true, page: 1, size: 1000}},
    {enabled: needsConstraintCheck},
  );

  if (props.group.deleted_at != null) {
    return null;
  }

  // Admins can always add the role to groups (even an externally managed role).
  if (!isAdmin) {
    // A non-admin must own at least one managed, non-role group to have somewhere to add the role.
    if (!isRoleOwner || ownedTargetGroups.length === 0) {
      return null;
    }
    if (needsConstraintCheck) {
      // Hide until the managed groups (with tags) load, so we don't flash a button we may remove.
      if (managedGroupsLoading || managedGroupsData == null) {
        return null;
      }
      const ownedTargetIds = new Set(ownedTargetGroups.map((group) => group!.id));
      const fetchedOwnedGroups = managedGroupsData.items.filter((item: GroupSummary) => ownedTargetIds.has(item.id));
      const someOwnedGroupAddable = fetchedOwnedGroups.some(
        (group: GroupSummary) => !ownerCantAddSelfGroups([group], owner),
      );
      // If the list was truncated (more managed groups than one page holds) we can't see every
      // owned group's tags — don't hide the button on incomplete data.
      const listComplete = managedGroupsData.items.length >= managedGroupsData.total;
      if (!someOwnedGroupAddable && listComplete) {
        return null;
      }
    }
  }

  return (
    <>
      <AddGroupsButton setOpen={setOpen} owner={owner} />
      {open ? (
        <AddGroupsDialog currentUser={props.currentUser} group={props.group} owner={owner} setOpen={setOpen} />
      ) : null}
    </>
  );
}

AddGroups.defaultProps = {
  owner: false,
};
