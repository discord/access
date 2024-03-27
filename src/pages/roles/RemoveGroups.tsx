import * as React from 'react';
import {useNavigate} from 'react-router-dom';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogContentText from '@mui/material/DialogContentText';
import DialogTitle from '@mui/material/DialogTitle';
import Alert from '@mui/material/Alert';
import CircularProgress from '@mui/material/CircularProgress';

import {usePutRoleMembersById, PutRoleMembersByIdError, PutRoleMembersByIdVariables} from '../../api/apiComponents';
import {PolymorphicGroup, RoleGroup, RoleMember} from '../../api/apiSchemas';

export interface RemoveGroupsDialogParameters {
  group: PolymorphicGroup;
  role: RoleGroup;
  owner: boolean;
}

interface RemoveGroupsDialogProps extends RemoveGroupsDialogParameters {
  setOpen(open: boolean): any;
}

const GROUP_TYPE_ID_TO_LABELS: Record<string, string> = {
  okta_group: 'Group',
  app_group: 'App Group',
  role_group: 'Role',
} as const;

export default function RemoveGroupsDialog(props: RemoveGroupsDialogProps) {
  const navigate = useNavigate();

  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);

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

  const submit = () => {
    setSubmitting(true);

    const roleMembers: RoleMember = {
      groups_to_add: [],
      groups_to_remove: [],
      owner_groups_to_add: [],
      owner_groups_to_remove: [],
    };

    if (props.owner) {
      roleMembers.owner_groups_to_remove = [props.group?.id ?? ''];
    } else {
      roleMembers.groups_to_remove = [props.group?.id ?? ''];
    }

    putGroupUsers.mutate({
      body: roleMembers,
      pathParams: {roleId: props.role?.id ?? ''},
    });
  };

  return (
    <Dialog open fullWidth onClose={() => props.setOpen(false)}>
      <DialogTitle>Remove Role Members</DialogTitle>
      <DialogContent>
        {requestError != '' ? <Alert severity="error">{requestError}</Alert> : null}
        <DialogContentText>
          Are you sure you want to remove all role <b>{props.role.name}</b> members from{' '}
          {GROUP_TYPE_ID_TO_LABELS[props.group.type].toLowerCase()} <b>{props.group.name}</b>{' '}
          {props.owner ? 'ownership' : 'membership'}?
        </DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => props.setOpen(false)}>Cancel</Button>
        <Button onClick={submit} type="submit" disabled={submitting}>
          {submitting ? <CircularProgress size={24} /> : 'Remove'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
