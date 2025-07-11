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
  useGetGroups,
  usePutRoleMembersById,
  PutRoleMembersByIdError,
  PutRoleMembersByIdVariables,
} from '../../api/apiComponents';
import {PolymorphicGroup, AppGroup, OktaGroup, RoleMember, OktaUser} from '../../api/apiSchemas';
import {isAccessAdmin, isGroupOwner} from '../../authorization';
import {minTagTimeGroups, requiredReasonGroups, ownerCantAddSelf} from '../../helpers';
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
  currentUser: OktaUser;
  owner: boolean;
  group: PolymorphicGroup;
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

const RFC822_FORMAT = 'ddd, DD MMM YYYY HH:mm:ss ZZ';

const UNTIL_ID_TO_LABELS: Record<string, string> = accessConfig.ACCESS_TIME_LABELS;
const UNTIL_JUST_NUMERIC_ID_TO_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries(UNTIL_ID_TO_LABELS).filter(([key]) => !isNaN(Number(key))),
);
const UNTIL_OPTIONS = Object.entries(UNTIL_ID_TO_LABELS).map(([id, label], index) => ({id: id, label: label}));

function AddGroupsDialog(props: AddGroupsDialogProps) {
  const navigate = useNavigate();
  const currentUser = useCurrentUser();

  const disallowedGroups = (
    currentUser.active_group_ownerships?.reduce((out, curr) => {
      curr != null && curr.active_group != null ? out.push(curr.active_group) : null;
      return out;
    }, new Array<OktaGroup>()) ?? []
  )
    .filter((group) =>
      ownerCantAddSelf(
        group.active_group_tags?.map((tagMap) => tagMap.active_tag!),
        props.owner,
      ),
    )
    .map((group) => group.id!);

  const currUserRoleGroupMember =
    props.group.active_user_memberships?.map((membership) => membership.active_user!.id).includes(currentUser.id) ??
    false;

  const [until, setUntil] = React.useState(accessConfig.DEFAULT_ACCESS_TIME);
  const [groupSearchInput, setGroupSearchInput] = React.useState('');
  const [groups, setGroups] = React.useState<Array<OktaGroup | AppGroup>>([]);
  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);
  const [labels, setLabels] = React.useState<Array<Record<string, string>>>(UNTIL_OPTIONS);
  const [timeLimit, setTimeLimit] = React.useState<number | null>(null);
  const [requiredReason, setRequiredReason] = React.useState<boolean>(false);

  const complete = (
    completedUsersChange: RoleMember | undefined,
    error: PutRoleMembersByIdError | null,
    variables: PutRoleMembersByIdVariables,
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

  const putGroupUsers = usePutRoleMembersById({
    onSettled: complete,
  });

  const {data: groupSearchData} = useGetGroups({
    queryParams: {
      page: 0,
      per_page: 10,
      managed: true,
      q: groupSearchInput,
    },
  });
  const groupSearchOptions = groupSearchData?.results ?? [];

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
        roleMembers.groups_added_ending_at = (groupsForm.customUntil as unknown as Dayjs).format(RFC822_FORMAT);
        break;
      default:
        roleMembers.groups_added_ending_at = dayjs()
          .add(parseInt(until ?? '0', 10), 'seconds')
          .format(RFC822_FORMAT);
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
          <Typography variant="subtitle1" color="text.accent">
            {timeLimit
              ? (props.owner ? 'Ownership of ' : 'Membership to ') +
                'one or more selected groups is limited to ' +
                Math.floor(timeLimit / 86400) +
                ' days.'
              : null}
          </Typography>
          <Typography variant="subtitle1" color="text.accent">
            {disallowedGroups.length != 0 && !isAccessAdmin(currentUser)
              ? // this case will never be hit as-is since admins are exempt from owner add constraints
                // leaving in in case we change the visibility of this dialog in the future
                'Some groups may not be added due to group tag constraints.'
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
              options={groupSearchOptions}
              autocompleteProps={{
                getOptionLabel: (option) => option.name,
                isOptionEqualToValue: (option, value) => option.id == value.id,
                filterOptions: (options) =>
                  options.filter(
                    (option) =>
                      option.type != 'role_group' &&
                      option.is_managed == true &&
                      (!groups.map((group) => group.id).includes(option.id) ||
                        (currUserRoleGroupMember && !isAccessAdmin(currentUser)
                          ? !disallowedGroups.includes(option.id!)
                          : false)),
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
  currentUser: OktaUser;
  group: PolymorphicGroup;
  owner?: boolean;
}

export default function AddGroups(props: AddGroupsProps) {
  const [open, setOpen] = React.useState(false);

  if (props.group.deleted_at != null || !isAccessAdmin(props.currentUser)) {
    return null;
  }

  return (
    <>
      <AddGroupsButton setOpen={setOpen} owner={props.owner ?? false} />
      {open ? (
        <AddGroupsDialog
          currentUser={props.currentUser}
          group={props.group}
          owner={props.owner ?? false}
          setOpen={setOpen}
        />
      ) : null}
    </>
  );
}

AddGroups.defaultProps = {
  owner: false,
};
