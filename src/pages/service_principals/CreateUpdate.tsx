import * as React from 'react';
import {useNavigate} from 'react-router-dom';
import AddIcon from '@mui/icons-material/AddBox';
import EditIcon from '@mui/icons-material/Edit';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import CircularProgress from '@mui/material/CircularProgress';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import FormControl from '@mui/material/FormControl';
import IconButton from '@mui/material/IconButton';
import TextField from '@mui/material/TextField';

import {useServicePrincipalByIdPut, useServicePrincipalsCreate} from '../../api/apiComponents';
import {OktaUserDetail, OktaUserSummary, ServicePrincipalDetail} from '../../api/apiSchemas';
import {isAccessAdmin} from '../../authorization';
import {PrincipalsField, RoleOption} from '../../components/Principals';

interface ServicePrincipalDialogProps {
  setOpen(open: boolean): any;
  servicePrincipal?: ServicePrincipalDetail;
}

function ServicePrincipalDialog(props: ServicePrincipalDialogProps) {
  const navigate = useNavigate();
  const existing = props.servicePrincipal;

  const [displayName, setDisplayName] = React.useState(existing?.display_name ?? '');
  const [subject, setSubject] = React.useState(existing?.subject ?? '');
  const [ownerUsers, setOwnerUsers] = React.useState<OktaUserSummary[]>(
    (existing?.owners ?? []).flatMap((o) => (o.active_user ? [o.active_user] : [])),
  );
  const [ownerRoles, setOwnerRoles] = React.useState<RoleOption[]>(
    (existing?.owners ?? []).flatMap((o) => (o.active_role_group ? [o.active_role_group] : [])),
  );
  const [requestError, setRequestError] = React.useState('');

  const onSettled = (result: ServicePrincipalDetail | undefined, error: {payload: any} | null) => {
    if (error != null) {
      setRequestError(error.payload.toString());
      return;
    }
    props.setOpen(false);
    if (existing != null) {
      navigate(0);
    } else {
      navigate('/service-principals/' + encodeURIComponent(result?.id ?? ''));
    }
  };
  const create = useServicePrincipalsCreate({onSettled});
  const update = useServicePrincipalByIdPut({onSettled});
  const submitting = create.isPending || update.isPending;

  const noOwners = ownerUsers.length == 0 && ownerRoles.length == 0;
  const canSubmit = displayName.trim() != '' && subject.trim() != '' && !noOwners && !submitting;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const body = {
      display_name: displayName.trim(),
      subject: subject.trim(),
      owner_user_ids: ownerUsers.map((u) => u.id),
      owner_role_ids: ownerRoles.map((r) => r.id),
    };
    if (existing == null) {
      create.mutate({body});
    } else {
      update.mutate({body, pathParams: {servicePrincipalId: existing.id}});
    }
  };

  const verb = existing == null ? 'Create' : 'Update';

  return (
    <Dialog open fullWidth onClose={() => props.setOpen(false)}>
      <form onSubmit={submit}>
        <DialogTitle>{verb} Service Principal</DialogTitle>
        <DialogContent>
          {requestError != '' ? <Alert severity="error">{requestError}</Alert> : null}
          <FormControl margin="normal" fullWidth>
            <TextField
              label="Display name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              slotProps={{htmlInput: {maxLength: 100}}}
              required
            />
          </FormControl>
          <FormControl margin="normal" fullWidth>
            <TextField
              label="Cloudflare service token client ID"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              helperText="Calls authenticated with this service token act as this service principal."
              slotProps={{htmlInput: {maxLength: 255}}}
              required
            />
          </FormControl>
          <PrincipalsField
            label="Owners"
            users={ownerUsers}
            roles={ownerRoles}
            onUsersChange={setOwnerUsers}
            onRolesChange={setOwnerRoles}
            rolesDisallowed={false}
            error={noOwners}
            helperText={
              noOwners
                ? 'Name at least one owning user or role.'
                : 'Owners are accountable for this service. They gain no permissions from it.'
            }
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => props.setOpen(false)}>Cancel</Button>
          <Button type="submit" disabled={!canSubmit}>
            {submitting ? <CircularProgress size={24} /> : verb}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

interface CreateUpdateServicePrincipalProps {
  currentUser: OktaUserDetail;
  servicePrincipal?: ServicePrincipalDetail;
}

export default function CreateUpdateServicePrincipal(props: CreateUpdateServicePrincipalProps) {
  const [open, setOpen] = React.useState(false);

  if (!isAccessAdmin(props.currentUser)) {
    return null;
  }

  return (
    <>
      {props.servicePrincipal == null ? (
        <Button variant="contained" onClick={() => setOpen(true)} endIcon={<AddIcon />}>
          Create Service Principal
        </Button>
      ) : (
        <IconButton aria-label="edit" onClick={() => setOpen(true)}>
          <EditIcon />
        </IconButton>
      )}
      {open ? <ServicePrincipalDialog setOpen={setOpen} servicePrincipal={props.servicePrincipal} /> : null}
    </>
  );
}
