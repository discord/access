import * as React from 'react';
import dayjs, {Dayjs} from 'dayjs';
import IsSameOrBefore from 'dayjs/plugin/isSameOrBefore';
import {useNavigate} from 'react-router-dom';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import RoleAddIcon from '@mui/icons-material/GroupAdd';
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

import {
  AutocompleteElement,
  DatePickerElement,
  FormContainer,
  SelectElement,
  TextFieldElement,
} from 'react-hook-form-mui';

import {
  useGetRoles,
  usePutRoleMembersById,
  PutRoleMembersByIdError,
  PutRoleMembersByIdVariables,
} from '../../api/apiComponents';
import {PolymorphicGroup, RoleGroup, RoleMember, OktaUser} from '../../api/apiSchemas';
import {canManageGroup, isAccessAdmin, isGroupOwner} from '../../authorization';
import {minTagTime, ownerCantAddSelf, requiredReason} from '../../helpers';
import {useCurrentUser} from '../../authentication';
import accessConfig from '../../config/accessConfig';

dayjs.extend(IsSameOrBefore);

interface AddRolesButtonProps {
  owner: boolean;
  setOpen(open: boolean): any;
}

function AddRolesButton(props: AddRolesButtonProps) {
  return (
    <Button variant="contained" onClick={() => props.setOpen(true)} endIcon={<RoleAddIcon />}>
      {'Add ' + (props.owner ? 'Roles as Owners' : 'Roles as Members')}
    </Button>
  );
}

interface AddRolesDialogProps {
  currentUser: OktaUser;
  owner: boolean;
  group: PolymorphicGroup;
  setOpen(open: boolean): any;
}

interface AddRolesForm {
  until?: string;
  customUntil?: string;
  user?: OktaUser;
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

function AddRolesDialog(props: AddRolesDialogProps) {
  const navigate = useNavigate();
  const currentUser = useCurrentUser();

  const currentUserRoleMembershipIds =
    currentUser.active_group_memberships
      ?.map((membership) => membership.active_group)
      .reduce((out, curr) => {
        curr != null && curr.type == 'role_group' ? out.push(curr.id!) : null;
        return out;
      }, new Array<string>()) ?? [];

  const [until, setUntil] = React.useState(accessConfig.DEFAULT_ACCESS_TIME);
  const [roleSearchInput, setRoleSearchInput] = React.useState('');
  const [roles, setRoles] = React.useState<Array<RoleGroup>>([]);
  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);

  const [rolesUpdatesCompleted, setRolesUpdatesCompleted] = React.useState(0);
  const [rolesUpdatesErrored, setRolesUpdatesErrored] = React.useState(0);

  const activeGroupTags = props.group.active_group_tags
    ? props.group.active_group_tags.map((tagMap) => tagMap.active_tag!)
    : [];

  // in seconds
  const timeLimit = minTagTime(activeGroupTags, props.owner);

  const reason = requiredReason(activeGroupTags, props.owner);

  // current user is a group owner and disallow owner add tag constraint active for type of dialog open (owner/member)
  const disallowOwnerAdd = isGroupOwner(currentUser, props.group.id!) && ownerCantAddSelf(activeGroupTags, props.owner);

  let labels = null;
  let timeLimitUntil = null;
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

    timeLimitUntil =
      timeLimit >= Number(accessConfig.DEFAULT_ACCESS_TIME)
        ? accessConfig.DEFAULT_ACCESS_TIME
        : Object.keys(filteredUntil).at(-1)!;

    labels = Object.entries(Object.assign({}, filteredUntil, {custom: 'Custom'})).map(([id, label], index) => ({
      id: id,
      label: label,
    }));
  }

  const complete = (
    completedUsersChange: RoleMember | undefined,
    error: PutRoleMembersByIdError | null,
    variables: PutRoleMembersByIdVariables,
    context: any,
  ) => {
    if (error != null) {
      setRequestError(error.payload.toString());
      setRolesUpdatesErrored((prevValue) => prevValue + 1);
    } else {
      setRolesUpdatesCompleted((prevValue) => prevValue + 1);
    }
  };

  React.useEffect(() => {
    if (submitting) {
      if (roles.length == rolesUpdatesCompleted + rolesUpdatesErrored) {
        setSubmitting(false);
        if (rolesUpdatesErrored > 0) {
          setRolesUpdatesCompleted(0);
          setRolesUpdatesErrored(0);
        } else {
          props.setOpen(false);
          navigate(0);
        }
      }
    }
  }, [rolesUpdatesCompleted, rolesUpdatesErrored]);

  const putGroupUsers = usePutRoleMembersById({
    onSettled: complete,
  });

  const {data: userSearchData} = useGetRoles({
    queryParams: {
      page: 0,
      per_page: 10,
      q: roleSearchInput,
    },
  });
  const userSearchOptions = userSearchData?.results ?? [];

  const submit = (rolesForm: AddRolesForm) => {
    setSubmitting(true);

    const roleMembers: RoleMember = {
      groups_to_add: [],
      groups_to_remove: [],
      owner_groups_to_add: [],
      owner_groups_to_remove: [],
    };

    if (props.owner) {
      roleMembers.owner_groups_to_add = [props.group?.id ?? ''];
    } else {
      roleMembers.groups_to_add = [props.group?.id ?? ''];
    }

    switch (rolesForm.until) {
      case 'indefinite':
        break;
      case 'custom':
        roleMembers.groups_added_ending_at = (rolesForm.customUntil as unknown as Dayjs).format(RFC822_FORMAT);
        break;
      default:
        roleMembers.groups_added_ending_at = dayjs()
          .add(parseInt(rolesForm.until ?? '0', 10), 'seconds')
          .format(RFC822_FORMAT);
        break;
    }

    roleMembers.created_reason = rolesForm.reason ?? '';

    roles.forEach((role) => {
      putGroupUsers.mutate({
        body: roleMembers,
        pathParams: {roleId: role.id ?? ''},
      });
    });
  };

  const removeGroupFromList = (userId: string) => {
    setRoles(roles.filter((user) => user.id != userId));
  };

  // Determine if a role should be disabled
  const isOptionDisabled = (option: RoleGroup) => {
    // Already in the list to be added
    if (roles.map((group) => group.id).includes(option.id)) {
      return true;
    }

    if (disallowOwnerAdd && !isAccessAdmin(currentUser)) {
      return currentUserRoleMembershipIds.includes(option.id!);
    }

    return false;
  };

  const addRolesText = props.owner ? 'Roles as Owners' : 'Roles as Members';
  const ownerOrMember = props.owner ? 'owner' : 'member';

  return (
    <Dialog open fullWidth onClose={() => props.setOpen(false)}>
      <FormContainer<AddRolesForm>
        defaultValues={timeLimit ? {until: timeLimitUntil!} : {until: accessConfig.DEFAULT_ACCESS_TIME}}
        onSuccess={(formData) => submit(formData)}>
        <DialogTitle>Add {addRolesText}</DialogTitle>
        <DialogContent>
          <Typography variant="subtitle1" color="text.accent">
            {timeLimit
              ? (props.owner ? 'Ownership of ' : 'Membership to ') +
                'this group is limited to ' +
                Math.floor(timeLimit / 86400) +
                ' days.'
              : null}
          </Typography>
          <Typography variant="subtitle1" color="text.accent">
            {disallowOwnerAdd && !isAccessAdmin(currentUser) ? (
              <>
                Certain roles cannot be added to this group due to the{' '}
                <strong>owner can't add themselves as {ownerOrMember}</strong> tag constraint. Please get another owner
                to add the role or, if you own the role, make a role request instead.
              </>
            ) : null}
          </Typography>
          {requestError != '' ? <Alert severity="error">{requestError}</Alert> : null}
          <FormControl size="small" margin="normal" fullWidth>
            <SelectElement
              label="For how long?"
              name="until"
              options={labels ?? UNTIL_OPTIONS}
              onChange={(value) => setUntil(value)}
              required
            />
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
              required={reason}
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
          <FormControl margin="normal" fullWidth>
            <AutocompleteElement
              label={'Search for Roles to Add'}
              name="user"
              options={userSearchOptions}
              autocompleteProps={{
                getOptionLabel: (option) => option.name,
                isOptionEqualToValue: (option, value) => option.id == value.id,
                getOptionDisabled: (option) => isOptionDisabled(option),
                onInputChange: (event, newInputValue, reason) => {
                  if (reason != 'reset') {
                    setRoleSearchInput(newInputValue);
                  }
                },
                onChange: (event, value) => {
                  if (value != null && !isOptionDisabled(value)) {
                    setRoles([value, ...roles]);
                    setRoleSearchInput('');
                  }
                },
                inputValue: roleSearchInput,
                renderOption: (props, option, state) => {
                  const disabled = isOptionDisabled(option);
                  return (
                    <li
                      {...props}
                      style={{
                        ...props.style,
                        opacity: disabled ? 0.5 : 1,
                        cursor: disabled ? 'not-allowed' : 'pointer',
                      }}>
                      <Grid container alignItems="center">
                        <Grid item>
                          <Box>{option.name}</Box>
                          <Typography variant="body2" color="text.secondary">
                            {GROUP_TYPE_ID_TO_LABELS[option.type]}
                            {disabled &&
                            disallowOwnerAdd &&
                            !isAccessAdmin(currentUser) &&
                            currentUserRoleMembershipIds.includes(option.id!)
                              ? ' (Cannot add due to tag constraint)'
                              : disabled && roles.map((group) => group.id).includes(option.id)
                                ? ' (Already selected)'
                                : ''}
                          </Typography>
                        </Grid>
                      </Grid>
                    </li>
                  );
                },
              }}
            />
          </FormControl>
          <FormControl margin="normal" fullWidth>
            <InputLabel shrink={roles.length > 0}>Roles to Add</InputLabel>
            <List
              sx={{
                overflow: 'auto',
                minHeight: 250,
                maxHeight: 600,
                backgroundColor: (theme) =>
                  theme.palette.mode === 'light' ? theme.palette.grey[100] : theme.palette.grey[900],
              }}
              dense={true}>
              {roles.map((group) => (
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
          <Button type="submit" disabled={submitting || roles.length === 0}>
            {submitting ? <CircularProgress size={24} /> : 'Add'}
          </Button>
        </DialogActions>
      </FormContainer>
    </Dialog>
  );
}

interface AddRolesProps {
  currentUser: OktaUser;
  group: PolymorphicGroup;
  owner?: boolean;
}

export default function AddRoles(props: AddRolesProps) {
  const [open, setOpen] = React.useState(false);

  if (
    props.group?.deleted_at != null ||
    !canManageGroup(props.currentUser, props.group) ||
    props.group.type == 'role_group' ||
    props.group?.is_managed == false
  ) {
    return null;
  }

  return (
    <>
      <AddRolesButton setOpen={setOpen} owner={props.owner ?? false} />
      {open ? (
        <AddRolesDialog
          currentUser={props.currentUser}
          group={props.group}
          owner={props.owner ?? false}
          setOpen={setOpen}
        />
      ) : null}
    </>
  );
}

AddRoles.defaultProps = {
  owner: false,
};
