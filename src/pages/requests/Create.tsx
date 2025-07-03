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
  DatePickerElement,
  TextFieldElement,
  ToggleButtonGroupElement,
} from 'react-hook-form-mui';

import {useGetGroups, useCreateRequest, CreateRequestError, CreateRequestVariables} from '../../api/apiComponents';
import {
  PolymorphicGroup,
  CreateAccessRequest,
  OktaUser,
  OktaGroup,
  AppGroup,
  AccessRequest,
  RoleGroup,
  RoleGroupMap,
} from '../../api/apiSchemas';
import {canManageGroup} from '../../authorization';
import {minTagTime, minTagTimeGroups} from '../../helpers';
import accessConfig from '../../config/accessConfig';

dayjs.extend(IsSameOrBefore);

interface CreateRequestButtonProps {
  setOpen(open: boolean): any;
  group?: PolymorphicGroup;
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
  currentUser: OktaUser;
  setOpen(open: boolean): any;
  setRecommendRoles(open: boolean): any;
  setGroup(group: PolymorphicGroup): any;
  setOwner(owner: boolean): any;
  group: PolymorphicGroup;
  owner: boolean;
}

function filterManagedRoleGroupMap(roleGroupMap: RoleGroupMap): boolean {
  return roleGroupMap.active_role_group?.is_managed ?? false;
}

function sortRoleGroupMap(aMap: RoleGroupMap, bMap: RoleGroupMap): number {
  let aName = aMap.active_role_group?.name ?? '';
  let bName = bMap.active_role_group?.name ?? '';
  return aName.localeCompare(bName);
}

function RecommendRolesContainer(props: RecommendRolesContainerProps) {
  const mappings =
    (props.owner ? props.group.active_role_owner_mappings : props.group.active_role_member_mappings) ?? [];

  const requestRole = (roleGroup: RoleGroup) => {
    props.setGroup(roleGroup);
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
              .map((mapping) => (
                <React.Fragment key={mapping.active_role_group?.id ?? ''}>
                  <ListItem
                    secondaryAction={
                      <Button
                        variant="contained"
                        onClick={() => requestRole(mapping.active_role_group ?? ({} as RoleGroup))}>
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
  currentUser: OktaUser;
  setOpen(open: boolean): any;
  group?: PolymorphicGroup;
  owner?: boolean;
  renew?: boolean;
}
interface CreateRequestForm {
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
  const [timeLimit, setTimeLimit] = React.useState<number | null>(
    props.group
      ? minTagTime(
          props.group.active_group_tags ? props.group.active_group_tags.map((tagMap) => tagMap.active_tag!) : [],
          props.owner ?? false,
        )
      : null,
  );
  const [groupSearchInput, setGroupSearchInput] = React.useState(props.group?.name ?? '');
  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);
  const [selectedGroup, setSelectedGroup] = React.useState<PolymorphicGroup | null>(props.group ?? null);
  const [owner, setOwner] = React.useState<boolean>(props.owner ?? false);

  const untilLabels: [string, Array<Record<string, string>>] = timeLimit
    ? filterUntilLabels(timeLimit)
    : [accessConfig.DEFAULT_ACCESS_TIME, UNTIL_OPTIONS];
  const [until, setUntil] = React.useState(untilLabels[0]);
  const [labels, setLabels] = React.useState<Array<Record<string, string>>>(untilLabels[1]);

  const complete = (
    completedRequest: AccessRequest | undefined,
    error: CreateRequestError | null,
    variables: CreateRequestVariables,
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

  const createRequest = useCreateRequest({
    onSettled: complete,
  });

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

    const accessRequest = {
      group_id: requestForm.group.id,
      group_owner: props.owner != null ? props.owner : requestForm.ownerOrMember == 'owner',
      reason: requestForm.reason ?? '',
    } as CreateAccessRequest;

    switch (requestForm.until) {
      case 'indefinite':
        break;
      case 'custom':
        accessRequest.ending_at = (requestForm.customUntil as unknown as Dayjs).format(RFC822_FORMAT);
        break;
      default:
        accessRequest.ending_at = dayjs()
          .add(parseInt(requestForm.until ?? '0', 10), 'seconds')
          .format(RFC822_FORMAT);
        break;
    }

    createRequest.mutate({body: accessRequest});
  };

  return (
    <FormContainer<CreateRequestForm>
      defaultValues={{
        group: props.group,
        until: accessConfig.DEFAULT_ACCESS_TIME,
        ownerOrMember: props.owner != null ? (props.owner ? 'owner' : 'member') : undefined,
      }}
      onSuccess={(formData) => submit(formData)}>
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
              Math.floor(timeLimit / 86400) +
              ' days.'
            : null}
        </Typography>
        {requestError != '' ? <Alert severity="error">{requestError}</Alert> : null}
        <FormControl margin="normal" fullWidth>
          <AutocompleteElement
            label={'For which group?'}
            name="group"
            options={groupSearchOptions}
            required
            autocompleteProps={{
              getOptionLabel: (option) => option.name,
              isOptionEqualToValue: (option, value) => option.id == value.id,
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
        <Button variant="contained" type="submit" disabled={submitting}>
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
          group={props.group ?? ({} as PolymorphicGroup)}
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
  currentUser: OktaUser;
  group?: PolymorphicGroup;
  owner?: boolean;
  renew?: boolean;
  expired?: boolean;
}

export default function CreateRequest(props: CreateRequestProps) {
  const [open, setOpen] = React.useState<boolean>(false);

  if (
    props.group?.deleted_at != null ||
    (props.group != null && canManageGroup(props.currentUser, props.group)) ||
    props.group?.is_managed == false
  ) {
    return null;
  }

  return (
    <>
      <CreateRequestButton
        setOpen={setOpen}
        group={props.group}
        owner={props.owner}
        renew={props.renew}
        expired={props.expired}></CreateRequestButton>
      {open ? <CreateRequestDialog setOpen={setOpen} {...props} renew={props.renew} /> : null}
    </>
  );
}
