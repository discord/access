import * as React from 'react';
import {useNavigate} from 'react-router-dom';

import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import ChangeReviewIcon from '@mui/icons-material/PublishedWithChanges';
import CircularProgress from '@mui/material/CircularProgress';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import FormControl from '@mui/material/FormControl';
import Grid from '@mui/material/Grid';
import InputLabel from '@mui/material/InputLabel';
import MenuItem from '@mui/material/MenuItem';
import Select from '@mui/material/Select';
import ToggleButton from '@mui/material/ToggleButton';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';

import AccessRequestIcon from '../../components/icons/MoreTime';

import {FormContainer, TextFieldElement} from 'react-hook-form-mui';
import {DatePickerElement} from 'react-hook-form-mui/date-pickers';

import {GridColDef, GridRenderCellParams} from '@mui/x-data-grid';
import {useTheme} from '@mui/material';

import dayjs, {Dayjs} from 'dayjs';

import {displayUserName} from '../../helpers';
import {useConstraintsForGroups} from '../../constraints';

import {useCurrentUser} from '../../authentication';

import {useRoleMembersByIdPut, RoleMembersByIdPutError, RoleMembersByIdPutVariables} from '../../api/apiComponents';
import {
  GroupRefForMembership,
  OktaUserGroupMemberDetail,
  RoleGroupMapDetail,
  RoleMember,
  RoleMembersSummary,
} from '../../api/apiSchemas';
import {isAccessAdmin} from '../../authorization';
import BulkRenewalDataGrid from '../../components/BulkRenewalDataGrid';
import accessConfig from '../../config/accessConfig';

interface Data {
  id: number;
  groupName: string | undefined;
  roleName: string | undefined;
  groupType: string | undefined;
  memberOrOwner: string;
  started: string;
  addedBy: string;
  ending: string;
  renew: 'yes' | 'no' | '';
  status: string;
}

function createData(row: RoleGroupMapDetail, selected: number | undefined, renewValue: 'yes' | 'no' | '' = ''): Data {
  const highlight = row.id == selected ? 'Selected-' : '';
  return {
    id: row.id!,
    groupName: row.group?.name,
    roleName: row.role_group?.name,
    groupType: row.group?.type == 'okta_group' ? 'Group' : row.group?.type == 'app_group' ? 'App Group' : 'Role Group',
    memberOrOwner: row.is_owner ? 'Owner' : 'Member',
    started: dayjs(row.created_at).startOf('second').fromNow(),
    addedBy: displayUserName(row.created_actor),
    ending: dayjs(row.ended_at).startOf('second').fromNow(),
    renew: renewValue,
    status: dayjs(row.ended_at).isBefore(dayjs())
      ? highlight + 'Expired'
      : row.should_expire
        ? highlight + 'Should-Expire'
        : dayjs(row.ended_at).isAfter(dayjs()) && dayjs(row.ended_at).isBefore(dayjs().add(7, 'day'))
          ? highlight + 'Soon'
          : highlight !== ''
            ? 'Selected'
            : '',
  };
}

interface CreateRequestForm {
  selectedYes: RoleGroupMapDetail[];
  selectedNo: RoleGroupMapDetail[];
  customUntil?: string;
  reason?: string;
}
const UNTIL_ID_TO_LABELS: Record<string, string> = accessConfig.ACCESS_TIME_LABELS;
const UNTIL_JUST_NUMERIC_ID_TO_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries(UNTIL_ID_TO_LABELS).filter(([key]) => !isNaN(Number(key))),
);
const UNTIL_OPTIONS = Object.entries(UNTIL_ID_TO_LABELS).map(([id, label], index) => ({id: id, label: label}));

interface BulkRenewalDialogProps {
  setOpen(open: boolean): any;
  rows: RoleGroupMapDetail[];
  select?: number;
}

type BlockedPair = [string, string, string]; // role group name, owner/member, group name

function BulkRenewalDialog(props: BulkRenewalDialogProps) {
  const navigate = useNavigate();
  const currentUser = useCurrentUser();

  const [requestError, setRequestError] = React.useState('');
  const [override, setOverride] = React.useState<boolean>(false);
  const [submitting, setSubmitting] = React.useState(false);

  const [numUpdates, setNumUpdates] = React.useState(0);
  const [groupUpdatesCompleted, setGroupUpdatesCompleted] = React.useState(0);
  const [groupUpdatesErrored, setGroupUpdatesErrored] = React.useState(0);

  const [labels, setLabels] = React.useState<Array<Record<string, string>>>(UNTIL_OPTIONS);

  // Track toggle states for each row
  const [toggleStates, setToggleStates] = React.useState<Record<number, 'yes' | 'no' | ''>>(() => {
    const initialStates: Record<number, 'yes' | 'no' | ''> = {};
    props.rows.forEach((row) => {
      if (row.should_expire) {
        initialStates[row.id!] = 'no';
      } else {
        initialStates[row.id!] = '';
      }
    });
    return initialStates;
  });

  const [selectedYes, setSelectedYes] = React.useState<RoleGroupMapDetail[]>(() =>
    props.select !== undefined ? props.rows.filter((r) => r.id === props.select) : [],
  );

  const [selectedNo, setSelectedNo] = React.useState<RoleGroupMapDetail[]>(() =>
    props.rows.filter((row) => row.should_expire),
  );

  const [until, setUntil] = React.useState(accessConfig.DEFAULT_ACCESS_TIME);

  const [paginationModel, setPaginationModel] = React.useState({
    pageSize: 10,
    page: props.select != undefined ? Math.ceil((props.rows.map((e) => e.id).indexOf(props.select) + 1) / 10) - 1 : 0,
  });

  const role_memberships =
    currentUser.active_group_memberships?.reduce((out: Set<string>, curr: OktaUserGroupMemberDetail) => {
      curr.active_group?.type == 'role_group' ? out.add(curr.active_group!.name!) : null;
      return out;
    }, new Set<string>()) ?? new Set<string>();

  // Per-row rather than coalesced: this marks which individual rows a self-add
  // restriction blocks, so it reads each group's own entry from `by_group`
  // instead of the roll-up.
  // A role can be associated with more groups than one request may name, so
  // this set is split across requests rather than truncated -- see
  // `useConstraintsForGroups`. Answering for only the first 200 rows would
  // drop the self-add warning on exactly the largest roles.
  const allRowGroupIds = React.useMemo(() => props.rows.map((row) => row.group?.id), [props.rows]);
  const rowConstraints = useConstraintsForGroups(allRowGroupIds);

  const rowDisallowsSelfAdd = (group: GroupRefForMembership | null | undefined, isOwner: boolean) =>
    rowConstraints.forGroup(group?.id).isSelfAddDisallowed(isOwner);

  const groups_cant_add_self_owner = props.rows.reduce((out, curr) => {
    rowDisallowsSelfAdd(curr.group, true) ? out.add(curr.group!.name) : null;
    return out;
  }, new Set<string>());
  const groups_cant_add_self_member = props.rows.reduce((out, curr) => {
    rowDisallowsSelfAdd(curr.group, false) ? out.add(curr.group!.name) : null;
    return out;
  }, new Set<string>());

  // Custom cell renderer for the ToggleButtonGroup
  const renderToggleButtons = (params: GridRenderCellParams) => {
    const handleToggleChange = (event: React.MouseEvent<HTMLElement>, newValue: string | null) => {
      const rowId = params.row.id;
      const currentValue = toggleStates[rowId] || '';

      // If clicking the same button, deselect it
      const finalValue = newValue === currentValue ? '' : (newValue as 'yes' | 'no' | '');

      setToggleStates((prev) => ({
        ...prev,
        [rowId]: finalValue,
      }));

      // Update selected arrays
      const rowData = props.rows.find((row) => row.id === rowId);
      if (!rowData) return;

      setSelectedYes((prev) => {
        const filtered = prev.filter((row) => row.id !== rowId);
        return finalValue === 'yes' ? [...filtered, rowData] : filtered;
      });

      setSelectedNo((prev) => {
        const filtered = prev.filter((row) => row.id !== rowId);
        return finalValue === 'no' ? [...filtered, rowData] : filtered;
      });
    };

    const currentValue = toggleStates[params.row.id] || '';

    // Determine if toggle should be disabled based on your conditional
    const isToggleEnabled = isAccessAdmin(currentUser)
      ? true
      : !(
          role_memberships.has(params.row.roleName) &&
          ((groups_cant_add_self_owner.has(params.row.groupName) && params.row.memberOrOwner == 'Owner') ||
            (groups_cant_add_self_member.has(params.row.groupName) && params.row.memberOrOwner == 'Member'))
        );

    return (
      <ToggleButtonGroup
        value={currentValue}
        exclusive
        onChange={handleToggleChange}
        aria-label="renew toggle"
        size="small"
        disabled={!isToggleEnabled}>
        <ToggleButton value="yes" aria-label="yes" disabled={!isToggleEnabled}>
          Yes
        </ToggleButton>
        <ToggleButton value="no" aria-label="no" disabled={!isToggleEnabled}>
          No
        </ToggleButton>
      </ToggleButtonGroup>
    );
  };

  const columns: GridColDef[] = [
    {field: 'roleName', headerName: 'Role Name', flex: 1},
    {field: 'groupName', headerName: 'Group Name', flex: 1},
    {field: 'groupType', headerName: 'Group Type', flex: 1},
    {field: 'memberOrOwner', headerName: 'Member or Owner', flex: 1},
    {field: 'started', headerName: 'Started', flex: 1},
    {field: 'addedBy', headerName: 'Added By', flex: 1},
    {field: 'ending', headerName: 'Ending', flex: 1},
    {
      field: 'renew',
      headerName: 'Renew?',
      flex: 1,
      renderCell: renderToggleButtons,
      sortable: false,
      filterable: false,
    },
  ];

  const display_owner_add_constraint =
    !isAccessAdmin(currentUser) &&
    props.rows.reduce((out, curr) => {
      return out || (role_memberships.has(curr.role_group?.name!) && rowDisallowsSelfAdd(curr.group, curr.is_owner!));
    }, false);

  // Which rows are actually being renewed, split by the side each was granted
  // on: a group's owner limit governs only the rows being renewed as
  // ownerships, and its member limit only the rest.
  const renewalMemberships = React.useMemo(
    () => [...selectedYes, ...selectedNo].filter((m) => toggleStates[m.id!] === 'yes'),
    [selectedYes, selectedNo, toggleStates],
  );
  const ownerGroupIds = React.useMemo(
    () => renewalMemberships.filter((m) => m.is_owner).map((m) => m.group?.id),
    [renewalMemberships],
  );
  const memberGroupIds = React.useMemo(
    () => renewalMemberships.filter((m) => !m.is_owner).map((m) => m.group?.id),
    [renewalMemberships],
  );

  // Two requests rather than one over the union: a request names one side, so
  // asking about every selected group at once would apply each group's owner
  // limit to rows that are only memberships.
  const ownerSideConstraints = useConstraintsForGroups(ownerGroupIds);
  const memberSideConstraints = useConstraintsForGroups(memberGroupIds);
  const constraintsBlocked = rowConstraints.blocked || ownerSideConstraints.blocked || memberSideConstraints.blocked;

  const ownerTimeLimit = ownerSideConstraints.timeLimit(true);
  const memberTimeLimit = memberSideConstraints.timeLimit(false);
  // Two different constraint keys governing two disjoint sets of rows, which
  // no single request can combine; choosing the tighter is the caller's call.
  const timeLimit =
    ownerTimeLimit == null
      ? memberTimeLimit
      : memberTimeLimit == null
        ? ownerTimeLimit
        : Math.min(ownerTimeLimit, memberTimeLimit);

  const requiredReason = ownerSideConstraints.isReasonRequired(true) || memberSideConstraints.isReasonRequired(false);

  // Bound the duration control to whatever limit is in force, and pull the
  // user's selection down if it now exceeds it.
  React.useEffect(() => {
    if (timeLimit == null) {
      setLabels(UNTIL_OPTIONS);
      return;
    }

    const filteredUntil = Object.keys(UNTIL_JUST_NUMERIC_ID_TO_LABELS)
      .filter((key) => Number(key) <= timeLimit)
      .reduce(
        (obj, key) => {
          obj[key] = UNTIL_JUST_NUMERIC_ID_TO_LABELS[key];
          return obj;
        },
        {} as Record<string, string>,
      );

    const currentUntilValue = until === 'custom' ? null : until === 'indefinite' ? Number.MAX_VALUE : Number(until);
    if (currentUntilValue === null || currentUntilValue > timeLimit) {
      setUntil(Object.keys(filteredUntil).at(-1)!);
    }

    setLabels(
      Object.entries(Object.assign({}, filteredUntil, {custom: 'Custom'})).map(([id, label]) => ({
        id: id,
        label: label,
      })),
    );
    // `until` is read but deliberately not a dependency: this reacts to the
    // limit changing, not to the user picking a duration.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeLimit]);

  const complete = (
    completedUsersChange: RoleMembersSummary | undefined,
    error: RoleMembersByIdPutError | null,
    variables: RoleMembersByIdPutVariables,
    context: any,
  ) => {
    if (error != null) {
      setRequestError(error.payload.toString());
      setGroupUpdatesErrored((prevValue) => prevValue + 1);
    } else {
      setGroupUpdatesCompleted((prevValue) => prevValue + 1);
    }
  };

  React.useEffect(() => {
    if (submitting) {
      if (numUpdates == groupUpdatesCompleted + groupUpdatesErrored) {
        setSubmitting(false);
        if (groupUpdatesErrored > 0) {
          setNumUpdates(0);
          setGroupUpdatesCompleted(0);
          setGroupUpdatesErrored(0);
        } else {
          props.setOpen(false);
          navigate(0);
        }
      }
    }
  }, [groupUpdatesCompleted, groupUpdatesErrored]);

  const putGroupUsers = useRoleMembersByIdPut({
    onSettled: complete,
  });

  const submit = (rolesForm: CreateRequestForm) => {
    let blockedRoles: Array<BlockedPair> = [];
    const blockedRoleSelected = selectedYes.reduce((out, roleGroupMap) => {
      const blockedOwner =
        role_memberships.has(roleGroupMap.role_group!.name) &&
        groups_cant_add_self_owner.has(roleGroupMap.group!.name) &&
        roleGroupMap.is_owner!;
      const blockedMember =
        role_memberships.has(roleGroupMap.role_group!.name) &&
        groups_cant_add_self_member.has(roleGroupMap.group!.name) &&
        !roleGroupMap.is_owner!;

      blockedOwner
        ? blockedRoles.push([roleGroupMap.role_group!.name, ' ownership of ', roleGroupMap.group!.name])
        : null;
      blockedMember
        ? blockedRoles.push([roleGroupMap.role_group!.name, ' membership to ', roleGroupMap.group!.name])
        : null;
      return out || blockedOwner || blockedMember;
    }, false);

    if (blockedRoleSelected && !override) {
      let blockedRolesString = '';
      const blockedLength = blockedRoles.length;

      for (let i = 0; i < blockedLength; i++) {
        blockedRolesString =
          blockedRolesString + blockedRoles[i][0] + "'s" + blockedRoles[i][1] + ' ' + blockedRoles[i][2];
        if (i < blockedLength - 1 && blockedLength > 2) blockedRolesString = blockedRolesString + ',';
        if (i < blockedLength - 1) blockedRolesString = blockedRolesString + ' ';
        if (i == blockedLength - 2 && blockedLength > 1) blockedRolesString = blockedRolesString + 'and ';
      }

      setRequestError(
        `Due to group constraints, you would normally be blocked from renewing ${blockedRolesString}. As an admin, you can override this. Click submit again if you would like to override the constraint. Deselect the row if not.`,
      );
      setOverride(true);
      return;
    }

    setSubmitting(true);

    if (
      selectedYes.length == 0 &&
      selectedNo.filter((n) => !n.should_expire && dayjs(n.ended_at) >= dayjs()).length == 0
    ) {
      setSubmitting(false);
      props.setOpen(false);
      navigate(0);
      return;
    }

    // group selected RoleGroupMaps by group
    // creates map  { role ids : {'owner' : [group ids], 'member' : [group ids]} }
    const grouped = selectedYes.reduce(
      (groups, item) => {
        (groups[item.role_group!.id!] ||= {owner: [], member: []})[item.is_owner ? 'owner' : 'member'].push(
          item.group!.id!,
        );
        return groups;
      },
      {} as Record<string, Record<string, string[]>>,
    );

    // group selectedNo RoleGroupMaps by group
    // creates map { role ids : [RoleGroupMapDetail] }
    // only include if access is active and decision has not already been made
    const doNotRenew = selectedNo.reduce(
      (groups, item) => {
        if (!item.should_expire && dayjs(item.ended_at) >= dayjs()) {
          (groups[item.role_group!.id!] ||= {owner: [], member: []})[item.is_owner ? 'owner' : 'member'].push(item.id!);
        }
        return groups;
      },
      {} as Record<string, Record<string, number[]>>,
    );

    const updates = new Set([...Object.keys(grouped), ...Object.keys(doNotRenew)]);

    setNumUpdates(updates.size);

    updates.forEach(function (gid) {
      const roleMembers: RoleMember = {
        groups_to_add: grouped[gid]?.['member'] ?? [],
        groups_should_expire: doNotRenew[gid]?.['member'] ?? [],
        groups_to_remove: [],
        owner_groups_to_add: grouped[gid]?.['owner'] ?? [],
        owner_groups_should_expire: doNotRenew[gid]?.['owner'] ?? [],
        owner_groups_to_remove: [],
      };

      switch (until) {
        case 'indefinite':
          break;
        case 'custom':
          roleMembers.groups_added_ending_at = (rolesForm.customUntil as unknown as Dayjs).toISOString();
          break;
        default:
          roleMembers.groups_added_ending_at = dayjs()
            .add(parseInt(until ?? '0', 10), 'seconds')
            .toISOString();
          break;
      }

      roleMembers.created_reason = rolesForm.reason ?? '';

      putGroupUsers.mutate({
        body: roleMembers,
        pathParams: {roleId: gid},
      });
    });
  };

  // Generate rows with current toggle states
  const dataRows = props.rows.map((row) => createData(row, props.select, toggleStates[row.id!] || ''));

  return (
    <Dialog open fullWidth maxWidth="xl" onClose={() => props.setOpen(false)}>
      <FormContainer<CreateRequestForm> onSuccess={(formData) => submit(formData)}>
        <DialogTitle>Bulk Renew Role Access</DialogTitle>
        <DialogContent>
          <Typography variant="subtitle1" color="text.accent">
            {timeLimit
              ? 'Access to one or more selected groups is limited to ' + Math.floor(timeLimit / 86400) + ' days.'
              : null}
          </Typography>
          <Typography variant="subtitle1" color="text.accent">
            {display_owner_add_constraint
              ? 'Due to group tag constraints, some roles may not be renewed since you are both a member of the role and an owner of the group. Please reach out to another group owner to renew the role membership to the group or create a role request if you own the role.'
              : null}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{mb: 2}}>
            Selected for renewal: {selectedYes.length} | Selected to allow expiration: {selectedNo.length}
          </Typography>
          {requestError != '' ? <Alert severity="error">{requestError}</Alert> : null}
          <Grid container spacing={1}>
            <Grid item xs={6}>
              <FormControl fullWidth sx={{margin: '7px 0'}}>
                <TextFieldElement
                  label="Why? (provide a reason)"
                  name="reason"
                  multiline
                  rows={1}
                  required={requiredReason}
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
            </Grid>
            <Grid item xs={3}>
              <Box>
                <FormControl margin="normal" fullWidth sx={{margin: '7px 0'}}>
                  <InputLabel>For how long?</InputLabel>
                  <Select
                    label="For how long?"
                    name="until"
                    value={until}
                    onChange={(event) => setUntil(event.target.value)}
                    required>
                    {labels.map((label) => {
                      return (
                        <MenuItem value={label.id} key={label.id}>
                          {label.label}
                        </MenuItem>
                      );
                    })}
                  </Select>
                </FormControl>
              </Box>
            </Grid>
            <Grid item xs={3}>
              {until == 'custom' ? (
                <Box>
                  <FormControl margin="normal" fullWidth required sx={{margin: '7px 0'}}>
                    <DatePickerElement
                      label="Custom End Date"
                      name="customUntil"
                      shouldDisableDate={(date: Dayjs) => date.isSameOrBefore(dayjs(), 'day')}
                      maxDate={timeLimit ? dayjs().add(timeLimit, 'second') : undefined}
                      required
                    />
                  </FormControl>
                </Box>
              ) : null}
            </Grid>
          </Grid>
          <BulkRenewalDataGrid
            rows={dataRows}
            rowHeight={45}
            columns={columns}
            columnVisibilityModel={{
              id: false,
              status: false, // used for color coding
            }}
            paginationModel={paginationModel}
            onPaginationModelChange={setPaginationModel}
            pageSizeOptions={[5, 10, 20]}
            disableRowSelectionOnClick
            hideFooterSelectedRowCount
            getRowClassName={(params) => (params.row.status != '' ? `super-app-theme--${params.row.status}` : '')}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => props.setOpen(false)}>Cancel</Button>
          <Button type="submit" disabled={submitting || constraintsBlocked}>
            {submitting ? <CircularProgress size={24} /> : 'Submit'}
          </Button>
        </DialogActions>
      </FormContainer>
    </Dialog>
  );
}

interface BulkRenewalButtonProps {
  setOpen(open: boolean): any;
  bulk: boolean;
  rereview?: boolean;
}

function BulkRenewalButton(props: BulkRenewalButtonProps) {
  const theme = useTheme();
  return (
    <Tooltip
      title={
        props.bulk
          ? 'Renew access for group memberships currently shown'
          : props.rereview && "Already reviewed and marked as 'Should expire.'"
      }>
      <span>
        <Button
          variant="contained"
          onClick={() => props.setOpen(true)}
          endIcon={props.rereview ? <ChangeReviewIcon /> : <AccessRequestIcon />}
          sx={{backgroundColor: props.rereview ? theme.palette.primary.dark : theme.palette.primary.main}}>
          {props.bulk ? 'Bulk Review' : props.rereview ? 'Update' : 'Review'}
        </Button>
      </span>
    </Tooltip>
  );
}

interface BulkRenewalProps {
  rows: RoleGroupMapDetail[];
  select?: number;
  rereview?: boolean;
}

export default function BulkRenewal(props: BulkRenewalProps) {
  const [open, setOpen] = React.useState(false);

  if (props.rows.length == 0) {
    return null;
  }

  return (
    <>
      <BulkRenewalButton setOpen={setOpen} bulk={props.select != undefined ? false : true} rereview={props.rereview} />
      {open ? <BulkRenewalDialog setOpen={setOpen} rows={props.rows} select={props.select} /> : null}
    </>
  );
}
