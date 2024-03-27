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

import {PutGroupMembersByIdError, PutGroupMembersByIdVariables, usePutGroupMembersById} from '../../api/apiComponents';
import {GroupMember, PolymorphicGroup} from '../../api/apiSchemas';

export interface RemoveOwnDirectAccessDialogParameters {
  userId: string;
  group: PolymorphicGroup;
  owner: boolean;
}

interface RemoveOwnDirectAccesssDialogProps extends RemoveOwnDirectAccessDialogParameters {
  setOpen(open: boolean): any;
}

export default function RemoveOwnDirectAccessDialog(props: RemoveOwnDirectAccesssDialogProps) {
  const navigate = useNavigate();

  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);

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
      props.setOpen(false);
      navigate(0);
    }
  };

  const putGroupUsers = usePutGroupMembersById({
    onSettled: complete,
  });

  const submit = () => {
    setSubmitting(true);

    const groupUsers: GroupMember = {
      members_to_add: [],
      members_to_remove: [],
      owners_to_add: [],
      owners_to_remove: [],
    };

    if (props.owner) {
      groupUsers.owners_to_remove = [props.userId];
    } else {
      groupUsers.members_to_remove = [props.userId];
    }

    putGroupUsers.mutate({
      body: groupUsers,
      pathParams: {groupId: props.group.id ?? ''},
    });
  };

  return (
    <Dialog open fullWidth onClose={() => props.setOpen(false)}>
      <DialogTitle>Remove Own Direct Access</DialogTitle>
      <DialogContent>
        {/* {requestError != '' ? <Alert severity="error">{requestError}</Alert> : null} */}
        <DialogContentText>
          Are you sure you want to remove <b>yourself</b> as {props.owner ? 'an owner' : 'a member'} of{' '}
          <b>{props.group.name}</b> ?
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
