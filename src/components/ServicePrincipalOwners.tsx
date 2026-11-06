import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';

import {ServicePrincipalOwnerDetail} from '../api/apiSchemas';
import {PrincipalChips} from './Principals';

interface ServicePrincipalOwnersProps {
  owners: ServicePrincipalOwnerDetail[] | undefined;
}

// Renders the active users and roles accountable for a service principal as links.
export default function ServicePrincipalOwners(props: ServicePrincipalOwnersProps) {
  const owners = props.owners ?? [];
  const users = owners.flatMap((owner) => (owner.active_user != null ? [owner.active_user] : []));
  const roles = owners.flatMap((owner) => (owner.active_role_group != null ? [owner.active_role_group] : []));
  if (users.length + roles.length == 0) {
    return (
      <Typography variant="body2" color="text.secondary">
        No active owners
      </Typography>
    );
  }
  return (
    <Stack direction="row" sx={{flexWrap: 'wrap', gap: 1}}>
      <PrincipalChips users={users} roles={roles} />
    </Stack>
  );
}
