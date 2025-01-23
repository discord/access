import * as React from 'react';
import {useNavigate} from 'react-router-dom';

import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import CircularProgress from '@mui/material/CircularProgress';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import Divider from '@mui/material/Divider';
import FormControl from '@mui/material/FormControl';
import Grid from '@mui/material/Grid';
import InputLabel from '@mui/material/InputLabel';
import MenuItem from '@mui/material/MenuItem';
import Select from '@mui/material/Select';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';

import {GridColDef} from '@mui/x-data-grid';

import dayjs, {Dayjs} from 'dayjs';

import {displayUserName} from '../../helpers';

import {OktaUserGroupMember, RoleGroup} from '../../api/apiSchemas';
import BulkRenewalDataGrid from '../../components/BulkRenewalDataGrid';

interface UserData {
  id: string;
  userName: string;
  userEmail: string | undefined;
  ending: string;
}

function createUserData(row: OktaUserGroupMember): UserData {
  return {
    id: row.active_user!.id,
    userName: displayUserName(row.active_user),
    userEmail: row.active_user?.email.toLowerCase(),
    ending: dayjs(row.ended_at).startOf('second').fromNow(),
  };
}

interface RoleMembersDialogProps {
  setOpen(open: boolean): any;
  rows: OktaUserGroupMember[];
  roleName: string;
  groupName: string;
  owner: boolean;
}

function RoleMembersDialog(props: RoleMembersDialogProps) {
  const [paginationModel, setPaginationModel] = React.useState({
    pageSize: 10,
    page: 0,
  });

  const columns: GridColDef[] = [
    {field: 'userName', headerName: 'User Name', flex: 1},
    {field: 'userEmail', headerName: 'User Email', flex: 1},
    {field: 'ending', headerName: 'Ending', flex: 1},
  ];

  return (
    <Dialog open fullWidth maxWidth="md" onClose={() => props.setOpen(false)}>
      <DialogTitle>{props.roleName} Members</DialogTitle>
      <DialogContent>
        {props.rows.length == 0 ? (
          <>
            <Divider sx={{borderRadius: 1, pt: 2}} />
            <Typography sx={{mt: 2}}>There are currently no members in this role.</Typography>
            <Typography sx={{mt: 2}} color="text.accent">
              If this request is approved, any members added to the role in the future will be added as{' '}
              {props.owner ? 'owners' : 'members'} of {props.groupName} automatically during the approved access period.
            </Typography>
          </>
        ) : (
          <>
            <Typography variant="subtitle1" color="text.accent">
              If the role request is approved, these users will be added as {props.owner ? 'owners' : 'members'} of{' '}
              {props.groupName}.
            </Typography>
            <BulkRenewalDataGrid
              rows={props.rows.map((row) => createUserData(row))}
              rowHeight={40}
              columns={columns}
              columnVisibilityModel={{
                id: false,
              }}
              paginationModel={paginationModel}
              onPaginationModelChange={setPaginationModel}
              pageSizeOptions={[5, 10, 20]}
              getRowClassName={(params) => (params.row.status != '' ? `super-app-theme--${params.row.status}` : '')}
            />
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={() => props.setOpen(false)}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}

interface BulkRenewalButtonProps {
  setOpen(open: boolean): any;
}

function RoleMembersButton(props: BulkRenewalButtonProps) {
  return (
    <Tooltip title={'Show list of role members'}>
      <Button variant="contained" onClick={() => props.setOpen(true)}>
        View role members
      </Button>
    </Tooltip>
  );
}

interface RoleMembersProps {
  rows: OktaUserGroupMember[];
  roleName: string;
  groupName: string;
  owner: boolean;
}

export default function RoleMembers(props: RoleMembersProps) {
  const [open, setOpen] = React.useState(false);

  return (
    <>
      <RoleMembersButton setOpen={setOpen} />
      {open && (
        <RoleMembersDialog
          setOpen={setOpen}
          rows={props.rows}
          roleName={props.roleName}
          groupName={props.groupName}
          owner={props.owner}
        />
      )}
    </>
  );
}
