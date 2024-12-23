import * as React from 'react';
import dayjs, {Dayjs} from 'dayjs';
import IsSameOrBefore from 'dayjs/plugin/isSameOrBefore';
import {useNavigate} from 'react-router-dom';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import UserAddIcon from '@mui/icons-material/PersonAdd';
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
  FormContainer,
  SelectElement,
  AutocompleteElement,
  DatePickerElement,
  TextFieldElement,
} from 'react-hook-form-mui';

import {
  useGetUsers,
  usePutGroupMembersById,
  PutGroupMembersByIdError,
  PutGroupMembersByIdVariables,
} from '../../api/apiComponents';
import {PolymorphicGroup, GroupMember, OktaUser, RoleGroup, OktaGroup, AppGroup} from '../../api/apiSchemas';
import {canManageGroup, isAccessAdmin} from '../../authorization';
import {
  displayUserName,
  minTagTime,
  minTagTimeGroups,
  ownerCantAddSelf,
  ownerCantAddSelfGroups,
  requiredReason,
  requiredReasonGroups,
} from '../../helpers';
import accessConfig from '../../config/accessConfig';

dayjs.extend(IsSameOrBefore);

interface AddUsersButtonProps {
  owner: boolean;
  setOpen(open: boolean): any;
}

function AddUsersButton(props: AddUsersButtonProps) {
  return (
    <Button variant="contained" onClick={() => props.setOpen(true)} endIcon={<UserAddIcon />}>
      {'Add ' + (props.owner ? 'Owners' : 'Members')}
    </Button>
  );
}

interface AddUsersDialogProps {
  currentUser: OktaUser;
  owner: boolean;
  group: PolymorphicGroup;
  setOpen(open: boolean): any;
}

interface AddUsersForm {
  until?: string;
  customUntil?: string;
  reason?: string;
}

const RFC822_FORMAT = 'ddd, DD MMM YYYY HH:mm:ss ZZ';

const UNTIL_ID_TO_LABELS: Record<string, string> = accessConfig.ACCESS_TIME_LABELS;
const UNTIL_JUST_NUMERIC_ID_TO_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries(UNTIL_ID_TO_LABELS).filter(([key]) => !isNaN(Number(key))),
);
const UNTIL_OPTIONS = Object.entries(UNTIL_ID_TO_LABELS).map(([id, label], index) => ({id: id, label: label}));

function AddUsersDialog(props: AddUsersDialogProps) {
  const navigate = useNavigate();

  const [until, setUntil] = React.useState(accessConfig.DEFAULT_ACCESS_TIME);
  const [userSearchInput, setUserSearchInput] = React.useState('');
  const [users, setUsers] = React.useState<Array<OktaUser>>([]);
  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);

  // in seconds
  let timeLimit = minTagTime(
    props.group.active_group_tags ? props.group.active_group_tags.map((tagMap) => tagMap.active_tag!) : [],
    props.owner,
  );

  let reason = requiredReason(
    props.group.active_group_tags ? props.group.active_group_tags.map((tagMap) => tagMap.active_tag!) : [],
    props.owner,
  );

  let disallow_owner_add = ownerCantAddSelf(
    props.group.active_group_tags ? props.group.active_group_tags.map((tagMap) => tagMap.active_tag!) : [],
    props.owner,
  );

  if (props.group.type == 'role_group' && !props.owner) {
    const active_groups_owners = (props.group as RoleGroup).active_role_associated_group_owner_mappings?.reduce(
      (out, curr) => {
        curr.active_group ? out.push(curr.active_group) : null;
        return out;
      },
      new Array<OktaGroup | AppGroup>(),
    );
    const active_groups_members = (props.group as RoleGroup).active_role_associated_group_member_mappings?.reduce(
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
    disallow_owner_add =
      disallow_owner_add ||
      ownerCantAddSelfGroups(active_groups_members ?? [], false) ||
      ownerCantAddSelfGroups(active_groups_owners ?? [], true);
  }

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
    completedUsersChange: GroupMember | undefined,
    error: PutGroupMembersByIdError | null,
    variables: PutGroupMembersByIdVariables,
    context: any,
  ) => {
    setSubmitting(false);
    if (error != null) {
      setRequestError(error.payload.toString());
    } else {
      props.setOpen(true);
      navigate(0);
    }
  };

  const putGroupUsers = usePutGroupMembersById({
    onSettled: complete,
  });

  const {data: userSearchData} = useGetUsers({
    queryParams: {
      page: 0,
      per_page: 10,
      q: userSearchInput,
    },
  });
  const userSearchOptions = userSearchData?.results ?? [];

  const submit = (usersForm: AddUsersForm) => {
    setSubmitting(true);

    const groupUsers: GroupMember = {
      members_to_add: [],
      members_to_remove: [],
      owners_to_add: [],
      owners_to_remove: [],
    };

    if (props.owner) {
      groupUsers.owners_to_add = users.map((user) => user.id);
    } else {
      groupUsers.members_to_add = users.map((user) => user.id);
    }

    switch (usersForm.until) {
      case 'indefinite':
        break;
      case 'custom':
        groupUsers.users_added_ending_at = (usersForm.customUntil as unknown as Dayjs).format(RFC822_FORMAT);
        break;
      default:
        groupUsers.users_added_ending_at = dayjs()
          .add(parseInt(usersForm.until ?? '0', 10), 'seconds')
          .format(RFC822_FORMAT);
        break;
    }

    groupUsers.created_reason = usersForm.reason ?? '';

    putGroupUsers.mutate({
      body: groupUsers,
      pathParams: {groupId: props.group?.id ?? ''},
    });
  };

  const removeUserFromList = (userId: string) => {
    setUsers(users.filter((user) => user.id != userId));
  };

  const addUsersText = props.owner ? 'Owners' : 'Members';

  return (
    <Dialog open fullWidth onClose={() => props.setOpen(false)}>
      <FormContainer<AddUsersForm>
        defaultValues={timeLimit ? {until: timeLimitUntil!} : {until: accessConfig.DEFAULT_ACCESS_TIME}}
        onSuccess={(formData) => submit(formData)}>
        <DialogTitle>Add {addUsersText}</DialogTitle>
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
            {disallow_owner_add && !isAccessAdmin(props.currentUser)
              ? 'Owners may not add themselves as ' + (props.owner ? 'owners' : 'members') + ' of this group.'
              : null}
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
          <FormControl fullWidth sx={{margin: '8px 0'}}>
            <AutocompleteElement
              label={'Search for ' + addUsersText + ' to Add'}
              name="user"
              options={userSearchOptions}
              autocompleteProps={{
                getOptionLabel: (option) => displayUserName(option),
                isOptionEqualToValue: (option, value) => option.id == value.id,
                filterOptions: (options) =>
                  options.filter((option) => {
                    const userIds = users.map((user) => user.id);
                    return (
                      !userIds.includes(option.id) &&
                      (disallow_owner_add && !isAccessAdmin(props.currentUser)
                        ? !(option.id == props.currentUser.id)
                        : true)
                    );
                  }),
                onInputChange: (event, newInputValue, reason) => {
                  if (reason != 'reset') {
                    setUserSearchInput(newInputValue);
                  }
                },
                onChange: (event, value) => {
                  if (value != null) {
                    setUsers([value, ...users]);
                    setUserSearchInput('');
                  }
                },
                inputValue: userSearchInput,
                renderOption: (props, option, state) => {
                  return (
                    <li {...props}>
                      <Grid container alignItems="center">
                        <Grid item>
                          <Box>{displayUserName(option)}</Box>
                          <Typography variant="body2" color="text.secondary">
                            {option.email.toLowerCase()}
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
            <InputLabel shrink={users.length > 0}>{addUsersText} to Add</InputLabel>
            <List
              sx={{
                overflow: 'auto',
                minHeight: 200,
                maxHeight: 400,
                backgroundColor: (theme) =>
                  theme.palette.mode === 'light' ? theme.palette.grey[100] : theme.palette.grey[900],
              }}
              dense={true}>
              {users.map((user) => (
                <React.Fragment key={user.id}>
                  <ListItem
                    sx={{py: 0}}
                    secondaryAction={
                      <IconButton edge="end" aria-label="delete" onClick={() => removeUserFromList(user.id)}>
                        <DeleteIcon />
                      </IconButton>
                    }>
                    <ListItemText primary={displayUserName(user)} secondary={user.email.toLowerCase()} />
                  </ListItem>
                  <Divider />
                </React.Fragment>
              ))}
            </List>
          </FormControl>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => props.setOpen(false)}>Cancel</Button>
          <Button type="submit" disabled={submitting || users.length === 0}>
            {submitting ? <CircularProgress size={24} /> : 'Add'}
          </Button>
        </DialogActions>
      </FormContainer>
    </Dialog>
  );
}

interface AddUsersProps {
  currentUser: OktaUser;
  group: PolymorphicGroup;
  owner?: boolean;
}

export default function AddUsers(props: AddUsersProps) {
  const [open, setOpen] = React.useState(false);

  if (
    props.group.deleted_at != null ||
    !canManageGroup(props.currentUser, props.group) ||
    props.group?.is_managed == false
  ) {
    return null;
  }

  return (
    <>
      <AddUsersButton setOpen={setOpen} owner={props.owner ?? false} />
      {open ? (
        <AddUsersDialog
          currentUser={props.currentUser}
          group={props.group}
          owner={props.owner ?? false}
          setOpen={setOpen}
        />
      ) : null}
    </>
  );
}

AddUsers.defaultProps = {
  owner: false,
};
