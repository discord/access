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
import AccessRequestIcon from '../../components/icons/MoreTime';
import Alert from '@mui/material/Alert';
import FormControl from '@mui/material/FormControl';
import Grid from '@mui/material/Grid';
import Typography from '@mui/material/Typography';
import Tooltip from '@mui/material/Tooltip';
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
  useGroups,
  useAccessRequestsCreate,
  AccessRequestsCreateError,
  AccessRequestsCreateVariables,
} from '../../api/apiComponents';
import {
  GroupDetail,
  CreateAccessRequestBody,
  OktaUserDetail,
  OktaGroupDetail,
  AppGroupDetail,
  AccessRequestDetail,
  RoleGroupDetail,
  RoleGroupMapDetail,
} from '../../api/apiSchemas';
import {canManageGroup, isAccessAdmin} from '../../authorization';
import {carriedConstraints, timeLimitLabel, useConstraintsForGroups} from '../../constraints';
import ConstraintsUnavailableAlert from '../../components/ConstraintsUnavailableAlert';
import accessConfig from '../../config/accessConfig';

dayjs.extend(IsSameOrBefore);

interface CreateRequestButtonProps {
  setOpen(open: boolean): any;
  group?: GroupDetail;
  owner?: boolean;
  renew?: boolean;
  expired?: boolean;
}

function CreateRequestButton(props: CreateRequestButtonProps) {
  return (
    <Tooltip title={props.expired && "Already reviewed and marked as 'Should expire'"}>
      <span>
        <Button variant="contained" onClick={() => props.setOpen(true)} endIcon={<AccessRequestIcon />}>
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

interface RecommendRolesContainerProps {
  currentUser: OktaUserDetail;
  setOpen(open: boolean): any;
  setRecommendRoles(open: boolean): any;
  setGroup(group: GroupDetail): any;
  setOwner(owner: boolean): any;
  group: GroupDetail;
  owner: boolean;
}

function filterManagedRoleGroupMap(roleGroupMap: RoleGroupMapDetail): boolean {
  return roleGroupMap.active_role_group?.is_managed ?? false;
}

function sortRoleGroupMap(aMap: RoleGroupMapDetail, bMap: RoleGroupMapDetail): number {
  let aName = aMap.active_role_group?.name ?? '';
  let bName = bMap.active_role_group?.name ?? '';
  return aName.localeCompare(bName);
}

function RecommendRolesContainer(props: RecommendRolesContainerProps) {
  const group = props.group as OktaGroupDetail;
  const mappings = (props.owner ? group.active_role_owner_mappings : group.active_role_member_mappings) ?? [];

  const requestRole = (roleGroup: RoleGroupDetail) => {
    props.setGroup(roleGroup as GroupDetail);
    // When requesting a recommended role, only request to be a member
    // as that is how to become either an owner or member of the associated group
    props.setOwner(false);
    props.setRecommendRoles(false);
  };

  return (
    <FormContainer onSuccess={(formData) => props.setRecommendRoles(false)}>
      <DialogTitle>Can we recommend a Role to request instead?</DialogTitle>
      <DialogContent>
        <FormControl margin="normal" fullWidth>
          <InputLabel shrink={true}>
            Available Roles with {props.owner == true ? 'Ownership' : 'Membership'} of {props.group?.name ?? ''}
          </InputLabel>
          <List
            sx={{
              overflow: 'auto',
              minHeight: 300,
              maxHeight: 600,
              backgroundColor: (theme) =>
                theme.palette.mode === 'light' ? theme.palette.grey[100] : theme.palette.grey[900],
            }}>
            {mappings
              .filter(filterManagedRoleGroupMap)
              .sort(sortRoleGroupMap)
              .map((mapping: RoleGroupMapDetail) => (
                <React.Fragment key={mapping.active_role_group?.id ?? ''}>
                  <ListItem
                    secondaryAction={
                      <Button
                        variant="contained"
                        onClick={() =>
                          requestRole(
                            (mapping.active_role_group as unknown as RoleGroupDetail) ?? ({} as RoleGroupDetail),
                          )
                        }>
                        Request
                      </Button>
                    }>
                    <ListItemText
                      primary={mapping.active_role_group?.name ?? ''}
                      secondary={GROUP_TYPE_ID_TO_LABELS[mapping.active_role_group?.type ?? 'okta_group']}
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
        <Button variant="contained" type="submit">
          Continue Direct {props.owner == true ? 'Ownership' : 'Membership'} Request
        </Button>
      </DialogActions>
    </FormContainer>
  );
}
interface CreateRequestContainerProps {
  currentUser: OktaUserDetail;
  setOpen(open: boolean): any;
  group?: GroupDetail;
  owner?: boolean;
  renew?: boolean;
}
interface CreateRequestForm {
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

const UNTIL_ID_TO_LABELS: Record<string, string> = accessConfig.ACCESS_TIME_LABELS;
const UNTIL_JUST_NUMERIC_ID_TO_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries(UNTIL_ID_TO_LABELS).filter(([key]) => !isNaN(Number(key))),
);
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

function CreateRequestContainer(props: CreateRequestContainerProps) {
  const navigate = useNavigate();

  // If a group is already selected by default and it has constraints limiting ownership or membership time,
  // find the shortest time (max allowed access time) and set that as the time limit. This value is used to
  // filter until drop-down labels, display a message about the constraint, and set a max date on the custom
  // until calendar.
  const [groupSearchInput, setGroupSearchInput] = React.useState(props.group?.name ?? '');
  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);
  const [selectedGroup, setSelectedGroup] = React.useState<GroupDetail | null>(props.group ?? null);
  const [owner, setOwner] = React.useState<boolean>(props.owner ?? false);

  // Seeded unrestricted; the effect below narrows both once the applicable
  // constraints arrive, including on first render for a group passed in as a
  // prop.
  const [until, setUntil] = React.useState(accessConfig.DEFAULT_ACCESS_TIME);
  const [labels, setLabels] = React.useState<Array<Record<string, string>>>(UNTIL_OPTIONS);

  // Owned here rather than by `FormContainer` so the constraint effect below
  // can move the `until` field when the allowed durations narrow.
  const formContext = useForm<CreateRequestForm>({
    defaultValues: {
      group: props.group,
      until: accessConfig.DEFAULT_ACCESS_TIME,
      ownerOrMember: props.owner != null ? (props.owner ? 'owner' : 'member') : undefined,
    },
  });

  const complete = (
    completedRequest: AccessRequestDetail | undefined,
    error: AccessRequestsCreateError | null,
    variables: AccessRequestsCreateVariables,
    context: any,
  ) => {
    setSubmitting(false);
    if (error != null) {
      setRequestError(error.payload.toString());
    } else {
      props.setOpen(false);
      navigate('/requests/' + encodeURIComponent(completedRequest?.id ?? ''));
    }
  };

  const createRequest = useAccessRequestsCreate({
    onSettled: complete,
  });

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

  // The API resolves what applies, including anything reaching a requested
  // *role* through its associations, so the picker never offers a duration the
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

    const accessRequest = {
      group_id: requestForm.group.id,
      group_owner: props.owner != null ? props.owner : requestForm.ownerOrMember == 'owner',
      reason: requestForm.reason ?? '',
    } as CreateAccessRequestBody;

    switch (requestForm.until) {
      case 'indefinite':
        break;
      case 'custom':
        accessRequest.ending_at = (requestForm.customUntil as unknown as Dayjs).toISOString();
        break;
      default:
        accessRequest.ending_at = dayjs()
          .add(parseInt(requestForm.until ?? '0', 10), 'seconds')
          .toISOString();
        break;
    }

    createRequest.mutate({body: accessRequest});
  };

  return (
    <FormContainer<CreateRequestForm> formContext={formContext} onSuccess={(formData) => submit(formData)}>
      <DialogTitle>
        {props.renew ? 'Renew ' : 'Create '}
        {props.owner != null ? (props.owner == true ? ' Ownership ' : ' Membership ') : ' Access '}
        Request
      </DialogTitle>
      <DialogContent>
        <Typography variant="subtitle1" color="text.accent">
          {timeLimit
            ? (owner ? 'Ownership of ' : 'Membership to ') +
              'this group is limited to ' +
              timeLimitLabel(timeLimit) +
              '.'
            : null}
        </Typography>
        {requestError != '' ? <Alert severity="error">{requestError}</Alert> : null}
        <ConstraintsUnavailableAlert constraints={constraints} action="sending" />
        <FormControl margin="normal" fullWidth>
          <AutocompleteElement<(typeof groupSearchOptions)[number]>
            label={'For which group?'}
            name="group"
            options={groupSearchOptions}
            required
            autocompleteProps={{
              getOptionLabel: (option) => option.name,
              isOptionEqualToValue: (option, value) => option.id == value?.id,
              filterOptions: (options) => options.filter((option) => option.is_managed == true),
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
        <Button variant="contained" type="submit" disabled={submitting || constraints.blocked}>
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
  const [recommendRoles, setRecommendRoles] = React.useState<boolean>(
    props.group != null
      ? props.group.type != 'role_group' &&
          (props.owner
            ? (props.group.active_role_owner_mappings?.filter(filterManagedRoleGroupMap).length ?? 0) > 0
            : (props.group.active_role_member_mappings?.filter(filterManagedRoleGroupMap).length ?? 0) > 0)
      : false,
  );

  return (
    <Dialog open onClose={() => props.setOpen(false)}>
      {recommendRoles ? (
        <RecommendRolesContainer
          {...props}
          setRecommendRoles={setRecommendRoles}
          group={props.group ?? ({} as GroupDetail)}
          owner={props.owner ?? false}
          setGroup={setGroup}
          setOwner={setOwner}
        />
      ) : (
        <CreateRequestContainer {...props} group={group} owner={owner} renew={props.renew} />
      )}
    </Dialog>
  );
}

interface CreateRequestProps {
  currentUser: OktaUserDetail;
  group?: GroupDetail;
  owner?: boolean;
  renew?: boolean;
  expired?: boolean;
  open?: boolean;
  setOpen?: (open: boolean) => void;
}

export default function CreateRequest(props: CreateRequestProps) {
  const [internalOpen, setInternalOpen] = React.useState<boolean>(false);
  const open = props.open ?? internalOpen;
  const setOpen = props.setOpen ?? setInternalOpen;

  // Managers of a group normally have no need to request access — they can add themselves
  // directly — so the button is hidden for them. The exception is a group tagged to disallow
  // owner self-add, where a request is their only path. Access admins are exempt from tag
  // constraints, so they never see it.
  // Not every caller hands this component a full `GroupDetail`: the expiring-
  // access page passes an audit group reference, which carries no
  // `effective_constraints` at all. `carriedConstraints` reads that absence as
  // unknown rather than as "nothing applies", so the button stays visible --
  // hiding it would take away the only path an owner a self-add restriction
  // blocks has to renew.
  const blockedFromSelfAdd =
    !isAccessAdmin(props.currentUser) &&
    carriedConstraints(props.group?.effective_constraints).isSelfAddDisallowed(props.owner ?? false);

  if (
    props.group?.deleted_at != null ||
    (props.group != null && canManageGroup(props.currentUser, props.group) && !blockedFromSelfAdd) ||
    props.group?.is_managed == false
  ) {
    return null;
  }

  return (
    <>
      {props.setOpen == null && (
        <CreateRequestButton
          setOpen={setOpen}
          group={props.group}
          owner={props.owner}
          renew={props.renew}
          expired={props.expired}
        />
      )}
      {open ? <CreateRequestDialog setOpen={setOpen} {...props} renew={props.renew} /> : null}
    </>
  );
}
