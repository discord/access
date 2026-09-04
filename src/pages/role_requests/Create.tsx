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
  TextFieldElement,
  ToggleButtonGroupElement,
} from 'react-hook-form-mui';
import {DatePickerElement} from 'react-hook-form-mui/date-pickers';
import {useForm} from 'react-hook-form';

import {
  useRoleRequestsCreate,
  useGroups,
  useRoles,
  RoleRequestsCreateError,
  RoleRequestsCreateVariables,
} from '../../api/apiComponents';
import {
  GroupDetail,
  CreateRoleRequestBody,
  OktaUserGroupMemberDetail,
  OktaUserDetail,
  OktaGroupDetail,
  AppGroupDetail,
  RoleRequestDetail,
  RoleRequestSummary,
  RoleGroupDetail,
  RoleGroupMapDetail,
} from '../../api/apiSchemas';
import {useCurrentUser} from '../../authentication';
import {canManageGroup} from '../../authorization';
import {useConstraintsForGroups} from '../../constraints';
import ConstraintsUnavailableAlert from '../../components/ConstraintsUnavailableAlert';
import {Tooltip} from '@mui/material';

dayjs.extend(IsSameOrBefore);

interface CreateRequestButtonProps {
  enabled: boolean;
  setOpen(open: boolean): any;
  role?: RoleGroupDetail;
  group?: GroupDetail;
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

function filterManagedRoleGroupMap(roleGroupMap: RoleGroupMapDetail): boolean {
  return roleGroupMap.active_role_group?.is_managed ?? false;
}

interface CreateRequestContainerProps {
  currentUser: OktaUserDetail;
  setOpen(open: boolean): any;
  role?: RoleGroupDetail;
  group?: GroupDetail;
  owner?: boolean;
  renew?: boolean;
}
interface CreateRequestForm {
  role: RoleGroupDetail;
  group: GroupDetail;
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
function getGroupIds(groups: Array<OktaUserGroupMemberDetail>): Array<string> {
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
  const [roleSearchInput, setRoleSearchInput] = React.useState(props.role?.name ?? '');
  const [groupSearchInput, setGroupSearchInput] = React.useState(props.group?.name ?? '');
  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);
  const [selectedGroup, setSelectedGroup] = React.useState<GroupDetail | null>(props.group ?? null);
  const [owner, setOwner] = React.useState<boolean>(props.owner ?? false);

  // Seeded unrestricted; the effect below narrows both once the applicable
  // constraints arrive, including on first render for a group passed in as a
  // prop.
  const [until, setUntil] = React.useState('1209600');
  const [labels, setLabels] = React.useState<Array<Record<string, string>>>(UNTIL_OPTIONS);

  // Owned here rather than by `FormContainer` so the constraint effect below
  // can move the `until` field when the allowed durations narrow.
  const formContext = useForm<CreateRequestForm>({
    defaultValues: {
      role: props.role,
      group: props.group,
      until: '1209600',
      ownerOrMember: props.owner != null ? (props.owner ? 'owner' : 'member') : undefined,
    },
  });

  const complete = (
    completedRequest: RoleRequestSummary | undefined,
    error: RoleRequestsCreateError | null,
    variables: RoleRequestsCreateVariables,
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

  const createRequest = useRoleRequestsCreate({
    onSettled: complete,
  });

  const {data: roleSearchData} = useRoles({
    queryParams: {
      page: 1,
      size: 10,
      q: roleSearchInput,
      owner_id: '@me',
    },
  });
  let roleSearchOptions = roleSearchData?.items ?? [];

  const {data: groupSearchData} = useGroups({
    queryParams: {
      page: 1,
      size: 10,
      q: groupSearchInput,
      managed: true,
    },
  });
  const groupSearchOptions = groupSearchData?.items ?? [];

  const updateUntil = (group: GroupDetail | null = selectedGroup, ownerOrMember: boolean = owner) => {
    setSelectedGroup(group);
    setOwner(ownerOrMember);
  };

  // The API resolves what applies, so the picker never offers a duration the
  // backend would quietly shorten.
  const constraints = useConstraintsForGroups([selectedGroup?.id]);
  const timeLimit = constraints.timeLimit(owner);

  React.useEffect(() => {
    // Only widen once the answer is known. While a refetch is in flight the
    // limit reads as null, and re-offering durations the group forbids makes
    // them briefly clickable -- the narrowing that follows then overwrites the
    // choice without saying so.
    if (constraints.blocked) {
      return;
    }
    if (timeLimit == null) {
      setLabels(UNTIL_OPTIONS);
      return;
    }
    const [filteredUntil, filteredLabels] = filterUntilLabels(timeLimit);
    setUntil(filteredUntil);
    setLabels(filteredLabels);
    // The form's own value has to move too, not just the option list. RHF
    // snapshots `defaultValues` at mount, so narrowing the options underneath
    // it leaves the field holding a duration no longer on offer -- the select
    // renders blank and a submit sends a length the backend then shortens
    // without saying so.
    formContext.setValue('until', filteredUntil);
  }, [timeLimit, constraints.blocked]);

  const submit = (requestForm: CreateRequestForm) => {
    setSubmitting(true);

    const roleRequest = {
      role_id: requestForm.role.id,
      group_id: requestForm.group.id,
      group_owner: props.owner != null ? props.owner : requestForm.ownerOrMember == 'owner',
      reason: requestForm.reason ?? '',
    } as CreateRoleRequestBody;

    switch (requestForm.until) {
      case 'indefinite':
        break;
      case 'custom':
        roleRequest.ending_at = (requestForm.customUntil as unknown as Dayjs).toISOString();
        break;
      default:
        roleRequest.ending_at = dayjs()
          .add(parseInt(requestForm.until ?? '0', 10), 'seconds')
          .toISOString();
        break;
    }

    createRequest.mutate({body: roleRequest});
  };

  return (
    <FormContainer<CreateRequestForm> formContext={formContext} onSuccess={(formData) => submit(formData)}>
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
        <ConstraintsUnavailableAlert constraints={constraints} action="sending" />
        <FormControl margin="normal" fullWidth>
          <AutocompleteElement<(typeof roleSearchOptions)[number]>
            label={'For which role?'}
            name="role"
            options={roleSearchOptions}
            required
            autocompleteProps={{
              getOptionLabel: (option) => option.name,
              isOptionEqualToValue: (option, value) => option.id == value?.id,
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
              // `readOnly`, not `disabled`: react-hook-form clears a disabled field's value, and
              // rhf-mui forwards `autocompleteProps.disabled` straight into `useController`, so
              // disabling this would submit the group as `undefined`. readOnly fixes the value
              // without dropping it.
              readOnly: props.group != null,
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
          <AutocompleteElement<(typeof groupSearchOptions)[number]>
            label={'For which group?'}
            name="group"
            options={groupSearchOptions}
            required
            autocompleteProps={{
              getOptionLabel: (option) => option.name,
              isOptionEqualToValue: (option, value) => option.id == value?.id,
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
              // `readOnly`, not `disabled`: react-hook-form clears a disabled field's value, and
              // rhf-mui forwards `autocompleteProps.disabled` straight into `useController`, so
              // disabling this would submit the group as `undefined`. readOnly fixes the value
              // without dropping it.
              readOnly: props.group != null,
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
              maxDate={timeLimit ? dayjs().add(timeLimit, 'second') : undefined}
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
            rules={{maxLength: 1024}}
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
        <Button type="submit" disabled={submitting || constraints.blocked}>
          {submitting ? <CircularProgress size={24} /> : 'Send'}
        </Button>
      </DialogActions>
    </FormContainer>
  );
}

interface CreateRequestDialogProps {
  currentUser: OktaUserDetail;
  setOpen(open: boolean): any;
  group?: GroupDetail;
  owner?: boolean;
  renew?: boolean;
}

function CreateRequestDialog(props: CreateRequestDialogProps) {
  const [group, setGroup] = React.useState<GroupDetail | undefined>(props.group);
  const [owner, setOwner] = React.useState<boolean | undefined>(props.owner);

  return (
    <Dialog open onClose={() => props.setOpen(false)}>
      <CreateRequestContainer {...props} group={group} owner={owner} renew={props.renew} />
    </Dialog>
  );
}

interface CreateRequestProps {
  enabled: boolean;
  currentUser: OktaUserDetail;
  role?: RoleGroupDetail;
  group?: OktaGroupDetail | AppGroupDetail;
  owner?: boolean;
  renew?: boolean;
  open?: boolean;
  setOpen?: (open: boolean) => void;
}

export default function CreateRequest(props: CreateRequestProps) {
  const [internalOpen, setInternalOpen] = React.useState<boolean>(false);
  const open = props.open ?? internalOpen;
  const setOpen = props.setOpen ?? setInternalOpen;

  if (
    props.role?.deleted_at != null ||
    props.group?.deleted_at != null ||
    (props.role != null && !canManageGroup(props.currentUser, props.role as GroupDetail)) ||
    props.group?.is_managed == false
  ) {
    return null;
  }

  return (
    <>
      {props.setOpen == null && (
        <CreateRequestButton
          enabled={props.enabled}
          setOpen={setOpen}
          role={props.role}
          group={props.group as GroupDetail | undefined}
          owner={props.owner}
          renew={props.renew}
        />
      )}
      {open ? (
        <CreateRequestDialog
          setOpen={setOpen}
          {...props}
          group={props.group as GroupDetail | undefined}
          renew={props.renew}
        />
      ) : null}
    </>
  );
}
