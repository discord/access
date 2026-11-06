import * as React from 'react';
import {useNavigate} from 'react-router-dom';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import CircularProgress from '@mui/material/CircularProgress';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogContentText from '@mui/material/DialogContentText';
import DialogTitle from '@mui/material/DialogTitle';
import IconButton from '@mui/material/IconButton';
import DeleteIcon from '@mui/icons-material/DeleteForever';

import {useServicePrincipalByIdDelete} from '../../api/apiComponents';
import {OktaUserDetail, ServicePrincipalDetail} from '../../api/apiSchemas';
import {isAccessAdmin} from '../../authorization';

interface DeleteDialogProps {
  setOpen(open: boolean): any;
  servicePrincipal: ServicePrincipalDetail;
}

function DeleteDialog(props: DeleteDialogProps) {
  const navigate = useNavigate();
  const [requestError, setRequestError] = React.useState('');

  const deleteServicePrincipal = useServicePrincipalByIdDelete({
    onSettled: (data, error) => {
      if (error != null) {
        setRequestError(error.payload.toString());
      } else {
        props.setOpen(false);
        navigate('/service-principals');
      }
    },
  });

  return (
    <Dialog open fullWidth onClose={() => props.setOpen(false)}>
      <DialogTitle>Delete Service Principal</DialogTitle>
      <DialogContent>
        {requestError != '' ? <Alert severity="error">{requestError}</Alert> : null}
        <DialogContentText>
          Are you sure you want to delete service principal <b>"{props.servicePrincipal.display_name}"</b>? Its
          credential will no longer authenticate as this service principal.
        </DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => props.setOpen(false)}>Cancel</Button>
        <Button
          onClick={() => deleteServicePrincipal.mutate({pathParams: {servicePrincipalId: props.servicePrincipal.id}})}
          disabled={deleteServicePrincipal.isPending}>
          {deleteServicePrincipal.isPending ? <CircularProgress size={24} /> : 'Delete'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

interface DeleteServicePrincipalProps {
  currentUser: OktaUserDetail;
  servicePrincipal: ServicePrincipalDetail;
}

export default function DeleteServicePrincipal(props: DeleteServicePrincipalProps) {
  const [open, setOpen] = React.useState(false);

  if (!isAccessAdmin(props.currentUser)) {
    return null;
  }

  return (
    <>
      <IconButton aria-label="delete" onClick={() => setOpen(true)}>
        <DeleteIcon />
      </IconButton>
      {open ? <DeleteDialog setOpen={setOpen} servicePrincipal={props.servicePrincipal} /> : null}
    </>
  );
}
