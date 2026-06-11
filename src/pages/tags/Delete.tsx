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

import {useTagByIdDelete, TagByIdDeleteError, TagByIdDeleteVariables} from '../../api/apiComponents';
import {TagDetail, DeleteMessage, OktaUserDetail} from '../../api/apiSchemas';
import {isAccessAdmin} from '../../authorization';

interface TagDialogProps {
  setOpen(open: boolean): any;
  tag: TagDetail;
}

function TagDialog(props: TagDialogProps) {
  const navigate = useNavigate();

  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);

  const complete = (
    deletedTag: DeleteMessage | undefined,
    error: TagByIdDeleteError | null,
    variables: TagByIdDeleteVariables,
    context: any,
  ) => {
    setSubmitting(false);
    if (error != null) {
      setRequestError(error.payload.toString());
    } else {
      props.setOpen(false);
      navigate('/tags/');
    }
  };

  const deleteTag = useTagByIdDelete({
    onSettled: complete,
  });

  const submit = () => {
    setSubmitting(true);
    deleteTag.mutate({
      pathParams: {tagId: props.tag?.id ?? ''},
    });
  };

  return (
    <Dialog open fullWidth onClose={() => props.setOpen(false)}>
      <DialogTitle>Delete Tag</DialogTitle>
      <DialogContent>
        {requestError != '' ? <Alert severity="error">{requestError}</Alert> : null}
        <DialogContentText>
          Are you sure you want to delete Tag <b>"{props.tag.name}"</b>?
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

interface DeleteTagProps {
  currentUser: OktaUserDetail;
  tag: TagDetail;
}

export default function DeleteTag(props: DeleteTagProps) {
  const [open, setOpen] = React.useState(false);

  if (props.tag.deleted_at != null || !isAccessAdmin(props.currentUser)) {
    return null;
  }

  return (
    <>
      <IconButton aria-label="edit" onClick={() => setOpen(true)}>
        <DeleteIcon />
      </IconButton>
      {open ? <TagDialog setOpen={setOpen} tag={props.tag}></TagDialog> : null}
    </>
  );
}
