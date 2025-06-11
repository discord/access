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

import {FormContainer, DatePickerElement, TextFieldElement} from 'react-hook-form-mui';

import {GridColDef, GridRenderCellParams} from '@mui/x-data-grid';
import {useTheme} from '@mui/material';

import dayjs, {Dayjs} from 'dayjs';

import {displayUserName, minTagTimeGroups, requiredReasonGroups} from '../../helpers';

import {usePutGroupMembersById, PutGroupMembersByIdError, PutGroupMembersByIdVariables} from '../../api/apiComponents';
import {GroupMember, OktaUserGroupMember, PolymorphicGroup, RoleGroupMap, RoleGroup} from '../../api/apiSchemas';
import BulkRenewalDataGrid from '../../components/BulkRenewalDataGrid';
import accessConfig from '../../config/accessConfig';

interface Data {
  id: number;
  userName: string;
  userEmail: string | undefined;
  groupName: string | undefined;
  memberOrOwner: string;
  started: string;
  addedBy: string;
  ending: string;
  renew: 'yes' | 'no' | '';
  status: string;
}

function createData(row: OktaUserGroupMember, selected: number | undefined, renewValue: 'yes' | 'no' | '' = ''): Data {
  const highlight = row.id == selected ? 'Selected-' : '';
  return {
    id: row.id,
    userName: displayUserName(row.user),
    userEmail: row.user?.email.toLowerCase(),
    groupName: row.group?.name,
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
  selectedYes: OktaUserGroupMember[];
  selectedNo: OktaUserGroupMember[];
  customUntil?: string;
  reason?: string;
}

const UNTIL_ID_TO_LABELS: Record<string, string> = accessConfig.ACCESS_TIME_LABELS;
const UNTIL_JUST_NUMERIC_ID_TO_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries(UNTIL_ID_TO_LABELS).filter(([key]) => !isNaN(Number(key))),
);
const UNTIL_OPTIONS = Object.entries(UNTIL_ID_TO_LABELS).map(([id, label], index) => ({id: id, label: label}));

const RFC822_FORMAT = 'ddd, DD MMM YYYY HH:mm:ss ZZ';

interface BulkRenewalDialogProps {
  setOpen(open: boolean): any;
  rows: OktaUserGroupMember[];
  select?: number;
}

function BulkRenewalDialog(props: BulkRenewalDialogProps) {
  const navigate = useNavigate();

  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);

  const [numUpdates, setNumUpdates] = React.useState(0);
  const [groupUpdatesCompleted, setGroupUpdatesCompleted] = React.useState(0);
  const [groupUpdatesErrored, setGroupUpdatesErrored] = React.useState(0);

  const [labels, setLabels] = React.useState<Array<Record<string, string>>>(UNTIL_OPTIONS);
  const [timeLimit, setTimeLimit] = React.useState<number | null>(null);
  const [requiredReason, setRequiredReason] = React.useState<boolean>(false);

  // Track toggle states for each row
  const [toggleStates, setToggleStates] = React.useState<Record<number, 'yes' | 'no' | ''>>(() => {
    const initialStates: Record<number, 'yes' | 'no' | ''> = {};
    props.rows.forEach((row) => {
      if (row.should_expire) {
        initialStates[row.id] = 'no';
      } else {
        initialStates[row.id] = '';
      }
    });
    return initialStates;
  });

  const [selectedYes, setSelectedYes] = React.useState<OktaUserGroupMember[]>(() =>
    props.select !== undefined ? props.rows.filter((r) => r.id === props.select) : [],
  );

  const [selectedNo, setSelectedNo] = React.useState<OktaUserGroupMember[]>(() =>
    props.rows.filter((row) => row.should_expire),
  );

  const [until, setUntil] = React.useState(accessConfig.DEFAULT_ACCESS_TIME);

  const [paginationModel, setPaginationModel] = React.useState({
    pageSize: 10,
    page: props.select !== undefined ? Math.ceil((props.rows.map((e) => e.id).indexOf(props.select) + 1) / 10) - 1 : 0,
  });

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

    return (
      <ToggleButtonGroup
        value={currentValue}
        exclusive
        onChange={handleToggleChange}
        aria-label="renew toggle"
        size="small">
        <ToggleButton value="yes" aria-label="yes">
          Yes
        </ToggleButton>
        <ToggleButton value="no" aria-label="no">
          No
        </ToggleButton>
      </ToggleButtonGroup>
    );
  };

  const columns: GridColDef[] = [
    {field: 'userName', headerName: 'User Name', flex: 1},
    {field: 'userEmail', headerName: 'User Email', flex: 1},
    {field: 'groupName', headerName: 'Group Name', flex: 1},
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

  const updateUntil = (memberships: OktaUserGroupMember[]) => {
    const ownedGroups = Array.from(
      memberships.reduce((all, curr) => {
        curr.is_owner ? all.add(curr.group) : null;
        return all;
      }, new Set<PolymorphicGroup>()),
    );

    const memberGroups = Array.from(
      memberships.reduce((all, curr) => {
        !curr.is_owner ? all.add(curr.group) : null;
        return all;
      }, new Set<PolymorphicGroup>()),
    );

    const owner_member = memberships.reduce((all, curr) => {
      return all.add(curr.is_owner);
    }, new Set<boolean>());

    let time: number | null = null;
    if (owner_member.size == 2) {
      const owner = minTagTimeGroups(ownedGroups, true) ?? Number.MAX_VALUE;
      const member = minTagTimeGroups(memberGroups, false) ?? Number.MAX_VALUE;
      time = owner == Number.MAX_VALUE && member == Number.MAX_VALUE ? null : Math.min(owner, member);
    } else if (owner_member.has(true)) {
      time = minTagTimeGroups(ownedGroups, true);
    } else {
      time = minTagTimeGroups(memberGroups, false) ?? Number.MAX_VALUE;
      time == Number.MAX_VALUE ? (time = null) : null;
    }

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

  const updateRequiredReason = (memberships: OktaUserGroupMember[]) => {
    const ownedGroups = Array.from(
      memberships.reduce((all, curr) => {
        curr.is_owner ? all.add(curr.group) : null;
        return all;
      }, new Set<PolymorphicGroup>()),
    );

    const memberGroups = Array.from(
      memberships.reduce((all, curr) => {
        !curr.is_owner ? all.add(curr.group) : null;
        return all;
      }, new Set<PolymorphicGroup>()),
    );

    // get role groups where renewing memberships only (not ownerships)
    const roleGroupMems = memberGroups.filter((group) => group.type == 'role_group');

    // role group ownerships
    const roleGroupOwnerGroups: PolymorphicGroup[] = (roleGroupMems as RoleGroup[])
      .reduce((out, rg) => {
        rg.active_role_associated_group_owner_mappings
          ? (out = out.concat(rg.active_role_associated_group_owner_mappings))
          : null;
        return out;
      }, new Array<RoleGroupMap>())
      .map((rgm) => rgm.active_group!);

    // role group memberships
    const roleGroupMemberGroups: PolymorphicGroup[] = (roleGroupMems as RoleGroup[])
      .reduce((out, rg) => {
        rg.active_role_associated_group_member_mappings
          ? (out = out.concat(rg.active_role_associated_group_member_mappings))
          : null;
        return out;
      }, new Array<RoleGroupMap>())
      .map((rgm) => rgm.active_group!);

    const owner_member = memberships.reduce((all, curr) => {
      return all.add(curr.is_owner!);
    }, new Set<boolean>());

    let req: boolean = false;
    if (owner_member.size == 2) {
      setRequiredReason(
        requiredReasonGroups(ownedGroups, true) ||
          requiredReasonGroups(memberGroups.concat(roleGroupMemberGroups), false) ||
          requiredReasonGroups(roleGroupOwnerGroups, true),
      );
    } else if (owner_member.has(true)) {
      setRequiredReason(requiredReasonGroups(ownedGroups, true));
    } else {
      setRequiredReason(
        requiredReasonGroups(memberGroups.concat(roleGroupMemberGroups), false) ||
          requiredReasonGroups(roleGroupOwnerGroups, true),
      );
    }
  };

  // Update time limits and required reason when selections change
  React.useEffect(() => {
    const allSelected = [...selectedYes, ...selectedNo];
    updateUntil(allSelected);
    updateRequiredReason(allSelected);
  }, [selectedYes, selectedNo]);

  const complete = (
    completedUsersChange: GroupMember | undefined,
    error: PutGroupMembersByIdError | null,
    variables: PutGroupMembersByIdVariables,
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

  const putGroupUsers = usePutGroupMembersById({
    onSettled: complete,
  });

  const submit = (requestForm: CreateRequestForm) => {
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

    // group selectedYes OktaUserGroupMembers by group
    // creates map { group ids : {'owner' : [user ids], 'member' : [user ids]} }
    const grouped = selectedYes.reduce(
      (groups, item) => {
        (groups[item.group.id!] ||= {owner: [], member: []})[item.is_owner ? 'owner' : 'member'].push(item.user.id);
        return groups;
      },
      {} as Record<string, Record<string, string[]>>,
    );

    // group selectedNo OktaUserGroupMembers by group
    // creates map { group ids : [OktaUserGroupMember] }
    // only include if access is active and decision has not already been made
    const doNotRenew = selectedNo.reduce(
      (groups, item) => {
        if (!item.should_expire && dayjs(item.ended_at) >= dayjs()) {
          (groups[item.group.id!] ||= {owner: [], member: []})[item.is_owner ? 'owner' : 'member'].push(item.id);
        }
        return groups;
      },
      {} as Record<string, Record<string, number[]>>,
    );

    const updates = new Set([...Object.keys(grouped), ...Object.keys(doNotRenew)]);

    setNumUpdates(updates.size);

    updates.forEach(function (gid) {
      const groupUsers: GroupMember = {
        members_to_add: grouped[gid]?.['member'] ?? [],
        members_should_expire: doNotRenew[gid]?.['member'] ?? [],
        members_to_remove: [],
        owners_to_add: grouped[gid]?.['owner'] ?? [],
        owners_should_expire: doNotRenew[gid]?.['owner'] ?? [],
        owners_to_remove: [],
      };

      switch (until) {
        case 'indefinite':
          break;
        case 'custom':
          groupUsers.users_added_ending_at = (requestForm.customUntil as unknown as Dayjs).format(RFC822_FORMAT);
          break;
        default:
          groupUsers.users_added_ending_at = dayjs()
            .add(parseInt(until ?? '0', 10), 'seconds')
            .format(RFC822_FORMAT);
          break;
      }

      groupUsers.created_reason = requestForm.reason ?? '';

      putGroupUsers.mutate({
        body: groupUsers,
        pathParams: {groupId: gid},
      });
    });
  };

  // Generate rows with current toggle states
  const dataRows = props.rows.map((row) => createData(row, props.select, toggleStates[row.id] || ''));

  return (
    <Dialog open fullWidth maxWidth="xl" onClose={() => props.setOpen(false)}>
      <FormContainer<CreateRequestForm> onSuccess={(formData) => submit(formData)}>
        <DialogTitle>Bulk Renew Group Access</DialogTitle>
        <DialogContent>
          <Typography variant="subtitle1" color="text.accent">
            {timeLimit
              ? 'Access to one or more selected groups is limited to ' + Math.floor(timeLimit / 86400) + ' days.'
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
                      maxDate={timeLimit ? dayjs().add(timeLimit, 'second') : null}
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
          <Button type="submit" disabled={submitting}>
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
  rows: OktaUserGroupMember[];
  select?: number;
  ownAccess?: boolean;
  rereview?: boolean;
}

export default function BulkRenewal(props: BulkRenewalProps) {
  const [open, setOpen] = React.useState(false);

  if (props.rows.length == 0 || props.ownAccess) {
    return null;
  }

  return (
    <>
      <BulkRenewalButton setOpen={setOpen} bulk={props.select != undefined ? false : true} rereview={props.rereview} />
      {open ? <BulkRenewalDialog setOpen={setOpen} rows={props.rows} select={props.select} /> : null}
    </>
  );
}
