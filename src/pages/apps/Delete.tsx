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

import {useDeleteAppById, DeleteAppByIdError, DeleteAppByIdVariables} from '../../api/apiComponents';
import {App, OktaUser} from '../../api/apiSchemas';
import {isAccessAdmin, isAppOwnerGroupOwner, ACCESS_APP_RESERVED_NAME} from '../../authorization';

interface AppDialogProps {
  setOpen(open: boolean): any;
  app: App;
}

function AppDialog(props: AppDialogProps) {
  const navigate = useNavigate();

  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);

  const complete = (
    deletedApp: App | undefined,
    error: DeleteAppByIdError | null,
    variables: DeleteAppByIdVariables,
    context: any,
  ) => {
    setSubmitting(false);
    if (error != null) {
      setRequestError(error.payload.toString());
    } else {
      props.setOpen(false);
      navigate('/apps/');
    }
  };

  const deleteApp = useDeleteAppById({
    onSettled: complete,
  });

  const submit = () => {
    setSubmitting(true);
    deleteApp.mutate({
      pathParams: {appId: props.app?.id ?? ''},
    });
  };

  return (
    <Dialog open fullWidth onClose={() => props.setOpen(false)}>
      <DialogTitle>Delete App</DialogTitle>
      <DialogContent>
        {requestError != '' ? <Alert severity="error">{requestError}</Alert> : null}
        <DialogContentText>
          Are you sure you want to delete App <b>"{props.app.name}"</b> and all related app groups?
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

interface DeleteAppProps {
  currentUser: OktaUser;
  app: App;
}

export default function DeleteApp(props: DeleteAppProps) {
  const [open, setOpen] = React.useState(false);

  if (
    !(isAccessAdmin(props.currentUser) || isAppOwnerGroupOwner(props.currentUser, props.app?.id ?? '')) ||
    props.app?.name == ACCESS_APP_RESERVED_NAME
  ) {
    return null;
  }

  return (
    <>
      <IconButton aria-label="edit" onClick={() => setOpen(true)}>
        <DeleteIcon />
      </IconButton>
      {open ? <AppDialog setOpen={setOpen} app={props.app}></AppDialog> : null}
    </>
  );
}
