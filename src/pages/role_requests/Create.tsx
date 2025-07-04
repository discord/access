import * as React from 'react';
import dayjs, {Dayjs} from 'dayjs';
import IsSameOrBefore from 'dayjs/plugin/isSameOrBefore';
import {useNavigate} from 'react-router-dom';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import InputLabel from '@mui/material/InputLabel';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemText from '@mui/material/ListItemText';
import Divider from '@mui/material/Divider';
import RoleRequestIcon from '@mui/icons-material/WorkHistory';
import Alert from '@mui/material/Alert';
import FormControl from '@mui/material/FormControl';
import Grid from '@mui/material/Grid';
import Typography from '@mui/material/Typography';
import Box from '@mui/material/Box';
import CircularProgress from '@mui/material/CircularProgress';

import {
  FormContainer,
  AutocompleteElement,
  SelectElement,
  DatePickerElement,
  TextFieldElement,
  ToggleButtonGroupElement,
} from 'react-hook-form-mui';

import {
  useCreateRoleRequest,
  useGetGroups,
  useGetRoles,
  CreateRoleRequestError,
  CreateRoleRequestVariables,
} from '../../api/apiComponents';
import {
  PolymorphicGroup,
  CreateRoleRequest,
  OktaUserGroupMember,
  OktaUser,
  OktaGroup,
  AppGroup,
  RoleRequest,
  RoleGroup,
  RoleGroupMap,
} from '../../api/apiSchemas';
import {useCurrentUser} from '../../authentication';
import {canManageGroup} from '../../authorization';
import {minTagTime, minTagTimeGroups} from '../../helpers';
import {Tooltip} from '@mui/material';

dayjs.extend(IsSameOrBefore);

interface CreateRequestButtonProps {
  enabled: boolean;
  setOpen(open: boolean): any;
  role?: RoleGroup;
  group?: PolymorphicGroup;
  owner?: boolean;
  renew?: boolean;
}

function CreateRequestButton(props: CreateRequestButtonProps) {
  return (
    <Tooltip
      title={
        props.enabled
          ? 'Request access on behalf of a role you own.'
          : 'You do not own any roles for which to request access.'
      }>
      <span>
        <Button
          variant="contained"
          onClick={() => props.setOpen(true)}
          endIcon={<RoleRequestIcon />}
          disabled={!props.enabled}>
          {props.group == null
            ? 'Create Request'
            : props.renew
              ? 'Renew'
              : props.owner
                ? 'Request Ownership'
                : 'Request Membership'}
        </Button>
      </span>
    </Tooltip>
  );
}

function filterManagedRoleGroupMap(roleGroupMap: RoleGroupMap): boolean {
  return roleGroupMap.active_role_group?.is_managed ?? false;
}

interface CreateRequestContainerProps {
  currentUser: OktaUser;
  setOpen(open: boolean): any;
  role?: RoleGroup;
  group?: PolymorphicGroup;
  owner?: boolean;
  renew?: boolean;
}
interface CreateRequestForm {
  role: RoleGroup;
  group: PolymorphicGroup;
  until?: string;
  customUntil?: string;
  ownerOrMember: string;
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

function filterUntilLabels(timeLimit: number): [string, Array<Record<string, string>>] {
  const filteredUntil = Object.keys(UNTIL_JUST_NUMERIC_ID_TO_LABELS)
    .filter((key) => Number(key) <= timeLimit!)
    .reduce(
      (obj, key) => {
        obj[key] = UNTIL_JUST_NUMERIC_ID_TO_LABELS[key];
        return obj;
      },
      {} as Record<string, string>,
    );

  const filteredLabeles = Object.entries(Object.assign({}, filteredUntil, {custom: 'Custom'})).map(
    ([id, label], index) => ({
      id: id,
      label: label,
    }),
  );

  return [Object.keys(filteredUntil).at(-1)!, filteredLabeles];
}

// Given an array of OktaUserGroupMembers, returns an array of group ids
function getGroupIds(groups: Array<OktaUserGroupMember>): Array<string> {
  return groups.reduce((ids, userGroupMember) => {
    if (userGroupMember.active_group?.id) {
      ids.push(userGroupMember.active_group.id);
    }
    return ids;
  }, new Array<string>());
}

function CreateRequestContainer(props: CreateRequestContainerProps) {
  const navigate = useNavigate();
  // Get array of ids of groups owned by the current user
  const ownedGroups = getGroupIds(useCurrentUser()?.active_group_ownerships ?? []);

  // If a group is already selected by default and it has constraints limiting ownership or membership time,
  // find the shortest time (max allowed access time) and set that as the time limit. This value is used to
  // filter until drop-down labels, display a message about the constraint, and set a max date on the custom
  // until calendar.
  const [timeLimit, setTimeLimit] = React.useState<number | null>(
    props.group
      ? minTagTime(
          props.group.active_group_tags ? props.group.active_group_tags.map((tagMap) => tagMap.active_tag!) : [],
          props.owner ?? false,
        )
      : null,
  );
  const [roleSearchInput, setRoleSearchInput] = React.useState(props.role?.name ?? '');
  const [groupSearchInput, setGroupSearchInput] = React.useState(props.group?.name ?? '');
  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);
  const [selectedGroup, setSelectedGroup] = React.useState<PolymorphicGroup | null>(props.group ?? null);
  const [owner, setOwner] = React.useState<boolean>(props.owner ?? false);

  const untilLabels: [string, Array<Record<string, string>>] = timeLimit
    ? filterUntilLabels(timeLimit)
    : ['1209600', UNTIL_OPTIONS];
  const [until, setUntil] = React.useState(untilLabels[0]);
  const [labels, setLabels] = React.useState<Array<Record<string, string>>>(untilLabels[1]);

  const complete = (
    completedRequest: RoleRequest | undefined,
    error: CreateRoleRequestError | null,
    variables: CreateRoleRequestVariables,
    context: any,
  ) => {
    setSubmitting(false);
    if (error != null) {
      setRequestError(error.payload.toString());
    } else {
      props.setOpen(false);
      navigate('/role-requests/' + encodeURIComponent(completedRequest?.id ?? ''));
    }
  };

  const createRequest = useCreateRoleRequest({
    onSettled: complete,
  });

  const {data: roleSearchData} = useGetRoles({
    queryParams: {
      page: 0,
      per_page: 10,
      q: roleSearchInput,
      owner_id: '@me',
    },
  });
  let roleSearchOptions = roleSearchData?.results ?? [];

  const {data: groupSearchData} = useGetGroups({
    queryParams: {
      page: 0,
      per_page: 10,
      q: groupSearchInput,
      managed: true,
    },
  });
  const groupSearchOptions = groupSearchData?.results ?? [];

  const updateUntil = (group: PolymorphicGroup | null = selectedGroup, ownerOrMember: boolean = owner) => {
    setSelectedGroup(group);
    setOwner(ownerOrMember);
    let time: number | null = null;
    if (group == null) {
      return;
    }

    // defaults to member if owner field on form is unset and props.owner == undefined
    time = minTagTime(
      group.active_group_tags ? group.active_group_tags.map((tagMap) => tagMap.active_tag!) : [],
      ownerOrMember,
    );

    setTimeLimit(time);

    if (!(time == null)) {
      const [filteredUntil, filteredLabels] = filterUntilLabels(time);

      setUntil(filteredUntil);
      setLabels(filteredLabels);
    } else {
      setLabels(UNTIL_OPTIONS);
    }
  };

  const submit = (requestForm: CreateRequestForm) => {
    setSubmitting(true);

    const roleRequest = {
      role_id: requestForm.role.id,
      group_id: requestForm.group.id,
      group_owner: props.owner != null ? props.owner : requestForm.ownerOrMember == 'owner',
      reason: requestForm.reason ?? '',
    } as CreateRoleRequest;

    switch (requestForm.until) {
      case 'indefinite':
        break;
      case 'custom':
        roleRequest.ending_at = (requestForm.customUntil as unknown as Dayjs).format(RFC822_FORMAT);
        break;
      default:
        roleRequest.ending_at = dayjs()
          .add(parseInt(requestForm.until ?? '0', 10), 'seconds')
          .format(RFC822_FORMAT);
        break;
    }

    createRequest.mutate({body: roleRequest});
  };

  return (
    <FormContainer<CreateRequestForm>
      defaultValues={{
        role: props.role,
        group: props.group,
        until: '1209600',
        ownerOrMember: props.owner != null ? (props.owner ? 'owner' : 'member') : undefined,
      }}
      onSuccess={(formData) => submit(formData)}>
      <DialogTitle>
        {props.renew ? 'Renew ' : 'Create '}
        {props.owner != null ? (props.owner == true ? ' Ownership ' : ' Membership ') : ' Role '}
        Request
      </DialogTitle>
      <DialogContent>
        <Typography variant="subtitle1" color="text.accent">
          {timeLimit
            ? (owner ? 'Ownership of ' : 'Membership to ') +
              'this group is limited to ' +
              Math.floor(timeLimit / 86400) +
              ' days.'
            : null}
        </Typography>
        {requestError != '' ? <Alert severity="error">{requestError}</Alert> : null}
        <FormControl margin="normal" fullWidth>
          <AutocompleteElement
            label={'For which role?'}
            name="role"
            options={roleSearchOptions}
            required
            autocompleteProps={{
              getOptionLabel: (option) => option.name,
              isOptionEqualToValue: (option, value) => option.id == value.id,
              onInputChange: (event, newInputValue, reason) => {
                if (reason != 'reset') {
                  setRoleSearchInput(newInputValue);
                }
              },
              onChange: (event, value) => {
                if (value != null) {
                  setRoleSearchInput(value.name);
                }
              },
              inputValue: roleSearchInput,
              disabled: props.group != null,
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
        <FormControl margin="normal" fullWidth>
          <AutocompleteElement
            label={'For which group?'}
            name="group"
            options={groupSearchOptions}
            required
            autocompleteProps={{
              getOptionLabel: (option) => option.name,
              isOptionEqualToValue: (option, value) => option.id == value.id,
              filterOptions: (options) =>
                options.filter((option) => option.is_managed == true && option.type != 'role_group'),
              onInputChange: (event, newInputValue, reason) => {
                if (reason != 'reset') {
                  setGroupSearchInput(newInputValue);
                }
              },
              onChange: (event, value) => {
                if (value != null) {
                  setGroupSearchInput(value.name);
                }
                updateUntil(value);
              },
              inputValue: groupSearchInput,
              disabled: props.group != null,
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
        <FormControl margin="normal" fullWidth>
          <Grid container>
            <Grid item xs={7}>
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
                required
                disabled={props.owner != null}
                onChange={(event, value) => {
                  updateUntil(undefined, value == 'owner');
                }}
                options={
                  props.owner != null
                    ? [
                        {
                          id: 'owner',
                          label: 'Owner',
                        },
                        {
                          id: 'member',
                          label: 'Member',
                        },
                      ]
                    : [
                        {
                          id: 'owner',
                          label: 'Owner',
                        },
                        {
                          id: 'member',
                          label: 'Member',
                        },
                      ]
                }
              />
            </Grid>
            <Grid item xs={1} />
          </Grid>
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
      </DialogContent>
      <DialogActions>
        <Button onClick={() => props.setOpen(false)}>Cancel</Button>
        <Button type="submit" disabled={submitting}>
          {submitting ? <CircularProgress size={24} /> : 'Send'}
        </Button>
      </DialogActions>
    </FormContainer>
  );
}

interface CreateRequestDialogProps {
  currentUser: OktaUser;
  setOpen(open: boolean): any;
  group?: PolymorphicGroup;
  owner?: boolean;
  renew?: boolean;
}

function CreateRequestDialog(props: CreateRequestDialogProps) {
  const [group, setGroup] = React.useState<PolymorphicGroup | undefined>(props.group);
  const [owner, setOwner] = React.useState<boolean | undefined>(props.owner);

  return (
    <Dialog open onClose={() => props.setOpen(false)}>
      <CreateRequestContainer {...props} group={group} owner={owner} renew={props.renew} />
    </Dialog>
  );
}

interface CreateRequestProps {
  enabled: boolean;
  currentUser: OktaUser;
  role?: RoleGroup;
  group?: OktaGroup | AppGroup;
  owner?: boolean;
  renew?: boolean;
}

export default function CreateRequest(props: CreateRequestProps) {
  const [open, setOpen] = React.useState<boolean>(false);

  if (
    props.role?.deleted_at != null ||
    props.group?.deleted_at != null ||
    (props.role != null && !canManageGroup(props.currentUser, props.role)) ||
    props.group?.is_managed == false
  ) {
    return null;
  }

  return (
    <>
      <CreateRequestButton
        enabled={props.enabled}
        setOpen={setOpen}
        role={props.role}
        group={props.group}
        owner={props.owner}
        renew={props.renew}></CreateRequestButton>
      {open ? <CreateRequestDialog setOpen={setOpen} {...props} renew={props.renew} /> : null}
    </>
  );
}
