import React from 'react';

import Avatar from '@mui/material/Avatar';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';

interface CreatedReasonButtonProps {
  setOpen(open: boolean): any;
}

function CreatedReasonButton(props: CreatedReasonButtonProps) {
  return (
    <Button variant="contained" onClick={() => props.setOpen(true)}>
      {'View'}
    </Button>
  );
}

interface CreatedReasonDialogProps {
  created_reason: string;
  setOpen(open: boolean): any;
}

function CreatedReasonDialog(props: CreatedReasonDialogProps) {
  return (
    <Dialog open fullWidth onClose={() => props.setOpen(false)}>
      <DialogTitle sx={{paddingBottom: '0px'}}>Justification</DialogTitle>
      <DialogContent>{props.created_reason}</DialogContent>
      <DialogActions>
        <Button onClick={() => props.setOpen(false)}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}

interface CreatedReasonProps {
  created_reason?: string;
}

export default function CreatedReason(props: CreatedReasonProps) {
  const [open, setOpen] = React.useState(false);

  if (!props.created_reason) {
    return null;
  }

  return (
    <>
      <CreatedReasonButton setOpen={setOpen} />
      {open ? <CreatedReasonDialog created_reason={props.created_reason} setOpen={setOpen} /> : null}
    </>
  );
}
