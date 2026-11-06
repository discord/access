import {Link as RouterLink} from 'react-router-dom';
import Chip from '@mui/material/Chip';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import UserIcon from '@mui/icons-material/Person';
import RoleIcon from '@mui/icons-material/Diversity3';

import {ServicePrincipalOwnerDetail} from '../../api/apiSchemas';
import {displayUserName} from '../../helpers';

interface ServicePrincipalOwnersProps {
  owners: ServicePrincipalOwnerDetail[] | undefined;
}

// Renders the active users and roles accountable for a service principal as links.
export default function ServicePrincipalOwners(props: ServicePrincipalOwnersProps) {
  const owners = (props.owners ?? []).filter((owner) => owner.active_user != null || owner.active_role_group != null);
  if (owners.length == 0) {
    return (
      <Typography variant="body2" color="text.secondary">
        No active owners
      </Typography>
    );
  }
  return (
    <Stack direction="row" sx={{flexWrap: 'wrap', gap: 1}}>
      {owners.map((owner) =>
        owner.active_user != null ? (
          <Chip
            key={'user-' + owner.active_user.id}
            icon={<UserIcon />}
            label={displayUserName(owner.active_user)}
            component={RouterLink}
            to={`/users/${owner.active_user.email.toLowerCase()}`}
            clickable
            variant="outlined"
          />
        ) : (
          <Chip
            key={'role-' + owner.active_role_group!.id}
            icon={<RoleIcon />}
            label={owner.active_role_group!.name}
            component={RouterLink}
            to={`/roles/${owner.active_role_group!.name}`}
            clickable
            variant="outlined"
          />
        ),
      )}
    </Stack>
  );
}
