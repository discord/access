import * as React from 'react';
import {useNavigate} from 'react-router-dom';
import Button from '@mui/material/Button';
import IconButton from '@mui/material/IconButton';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogContentText from '@mui/material/DialogContentText';
import DialogTitle from '@mui/material/DialogTitle';
import DeleteIcon from '@mui/icons-material/DeleteForever';
import Alert from '@mui/material/Alert';
import CircularProgress from '@mui/material/CircularProgress';

import {useDeleteGroupById, DeleteGroupByIdError, DeleteGroupByIdVariables} from '../../api/apiComponents';
import {PolymorphicGroup, AppGroup, OktaUser} from '../../api/apiSchemas';
import {canManageGroup} from '../../authorization';

interface GroupDialogProps {
  setOpen(open: boolean): any;
  group: PolymorphicGroup;
}

const GROUP_TYPE_ID_TO_LABELS: Record<string, string> = {
  okta_group: 'Group',
  app_group: 'App Group',
  role_group: 'Role',
} as const;

function GroupDialog(props: GroupDialogProps) {
  const navigate = useNavigate();

  const defaultGroupType = props.group?.type;
  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);

  const complete = (
    deletedGroup: PolymorphicGroup | undefined,
    error: DeleteGroupByIdError | null,
    variables: DeleteGroupByIdVariables,
    context: any,
  ) => {
    setSubmitting(false);
    if (error != null) {
      setRequestError(error.payload.toString());
    } else {
      props.setOpen(false);
      switch (props.group.type) {
        case 'app_group':
          navigate('/apps/' + encodeURIComponent((props.group as AppGroup).app?.name ?? ''));
          break;
        case 'role_group':
          navigate('/roles/');
          break;
        default:
          navigate('/groups/');
          break;
      }
    }
  };

  const deleteGroup = useDeleteGroupById({
    onSettled: complete,
  });

  const submit = () => {
    setSubmitting(true);
    deleteGroup.mutate({
      pathParams: {groupId: props.group?.id ?? ''},
    });
  };

  return (
    <Dialog open fullWidth onClose={() => props.setOpen(false)}>
      <DialogTitle>Delete {GROUP_TYPE_ID_TO_LABELS[props.group.type]}</DialogTitle>
      <DialogContent>
        {requestError != '' ? <Alert severity="error">{requestError}</Alert> : null}
        <DialogContentText>
          Are you sure you want to delete {GROUP_TYPE_ID_TO_LABELS[props.group.type].toLowerCase()}{' '}
          <b>"{props.group.name}"</b>?
        </DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => props.setOpen(false)}>Cancel</Button>
        <Button onClick={submit} type="submit" disabled={submitting}>
          {submitting ? <CircularProgress size={24} /> : 'Delete'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

interface DeleteGroupProps {
  currentUser: OktaUser;
  group: PolymorphicGroup;
}

export default function DeleteGroup(props: DeleteGroupProps) {
  const [open, setOpen] = React.useState(false);

  if (
    props.group.deleted_at != null ||
    !canManageGroup(props.currentUser, props.group) ||
    (props.group.type == 'app_group' && (props.group as AppGroup).is_owner) ||
    !props.group.is_managed
  ) {
    return null;
  }

  return (
    <>
      <IconButton aria-label="edit" onClick={() => setOpen(true)}>
        <DeleteIcon />
      </IconButton>
      {open ? <GroupDialog setOpen={setOpen} group={props.group}></GroupDialog> : null}
    </>
  );
}
