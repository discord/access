import {Link as RouterLink} from 'react-router-dom';
import Link from '@mui/material/Link';
import Tooltip from '@mui/material/Tooltip';
import ServicePrincipalIcon from '@mui/icons-material/SmartToy';

import {OktaUserSummary, ServicePrincipalDetail} from '../api/apiSchemas';
import {displayUserName} from '../helpers';

interface RequesterLinkProps {
  user?: OktaUserSummary | null;
  servicePrincipal?: ServicePrincipalDetail | null;
  // Show a user by email rather than by name.
  showEmail?: boolean;
  fontWeight?: number;
}

/** Display name for a request's requester, which is a user or a service principal. */
export function requesterName(
  user: OktaUserSummary | null | undefined,
  servicePrincipal: ServicePrincipalDetail | null | undefined,
): string {
  return servicePrincipal != null ? servicePrincipal.display_name : displayUserName(user);
}

/** Links a request's requester to its page: a user, or a service principal marked as a service. */
export default function RequesterLink(props: RequesterLinkProps) {
  const sx = {color: 'inherit', fontWeight: props.fontWeight};
  if (props.servicePrincipal != null) {
    return (
      <Tooltip title="Service principal">
        <Link
          to={`/service-principals/${props.servicePrincipal.id}`}
          sx={{...sx, textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: 0.5}}
          component={RouterLink}>
          <ServicePrincipalIcon fontSize="inherit" aria-label="service principal" />
          {props.servicePrincipal.display_name}
        </Link>
      </Tooltip>
    );
  }
  const user = props.user;
  const label = props.showEmail ? (user?.email.toLowerCase() ?? '') : displayUserName(user);
  if (user?.deleted_at != null) {
    return (
      <Link to={`/users/${user.id}`} sx={{...sx, textDecoration: 'line-through'}} component={RouterLink}>
        {label}
      </Link>
    );
  }
  return (
    <Link to={`/users/${user?.email.toLowerCase() ?? ''}`} sx={{...sx, textDecoration: 'none'}} component={RouterLink}>
      {label}
    </Link>
  );
}
