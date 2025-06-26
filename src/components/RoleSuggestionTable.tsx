import React from 'react';
import {Link as RouterLink} from 'react-router-dom';

import Link from '@mui/material/Link';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Typography from '@mui/material/Typography';
import Paper from '@mui/material/Paper';
import ArrowForwardIcon from '@mui/icons-material/ArrowForward';
import TablePagination from '@mui/material/TablePagination';
import Tooltip from '@mui/material/Tooltip';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import Box from '@mui/material/Box';

import {RoleGroupMap} from '../api/apiSchemas';
import Started from './Started';
import Ending from './Ending';

interface RoleSuggestionTableProps {
  roleMappings: RoleGroupMap[];
}

const ROLE_TOOLTIP =
  "It's preferable to grant access to a group via roles to ensure your configured roles provide access necessary for job functions. Direct access can be confusing and can lead to problems with auditing, offboarding, and maintaining least privilege.";

export default function RoleSuggestionTable({roleMappings}: RoleSuggestionTableProps) {
  // Filter to only show active role mappings
  const activeRoleMappings = roleMappings.filter(
    (mapping) => mapping.ended_at == null || new Date(mapping.ended_at) > new Date(),
  );

  // Pagination state
  const [page, setPage] = React.useState(0);
  const rowsPerPage = 5;
  const handleChangePage = (event: unknown, newPage: number) => {
    setPage(newPage);
  };

  if (activeRoleMappings.length === 0) {
    return null;
  }

  return (
    <Paper sx={{p: 2, mt: 1}}>
      <Box sx={{display: 'flex', alignItems: 'center', mb: 1}}>
        <Tooltip title={ROLE_TOOLTIP} placement="top" arrow>
          <InfoOutlinedIcon color="warning" sx={{mr: 1, fontSize: 24, verticalAlign: 'middle', cursor: 'pointer'}} />
        </Tooltip>
        <Typography variant="h6" color="warning.main" sx={{fontWeight: 500}}>
          Alternative Role-based Access Available
        </Typography>
      </Box>
      <Typography variant="body2" color="text.secondary" sx={{mb: 2}}>
        Consider granting access through one of these roles instead of direct access:
      </Typography>
      <Table size="small" aria-label="alternative role access">
        <TableHead>
          <TableRow>
            <TableCell>Role Name</TableCell>
            <TableCell>Access Type</TableCell>
            <TableCell>Started</TableCell>
            <TableCell>Ending</TableCell>
            <TableCell>Added by</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {activeRoleMappings.slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage).map((mapping) => {
            const role = mapping.active_role_group || mapping.role_group;
            const roleName = role?.name || 'Unknown Role';
            const roleId = role?.name || '';
            return (
              <TableRow key={mapping.id}>
                <TableCell>
                  {roleId ? (
                    <Link
                      to={`/roles/${roleId}`}
                      sx={{
                        textDecoration: 'none',
                        color: 'text.primary',
                        display: 'inline-flex',
                        alignItems: 'center',
                        cursor: 'pointer',
                        transition: 'text-decoration 0.2s',
                        '&:hover': {
                          textDecoration: 'underline',
                          color: 'primary.main',
                        },
                      }}
                      component={RouterLink}>
                      {roleName}
                      <ArrowForwardIcon fontSize="small" sx={{ml: 0.5}} />
                    </Link>
                  ) : (
                    <span>{roleName}</span>
                  )}
                </TableCell>
                <TableCell>{mapping.is_owner ? 'Owner' : 'Member'}</TableCell>
                <TableCell>
                  <Started memberships={[mapping]} />
                </TableCell>
                <TableCell>
                  <Ending memberships={[mapping]} />
                </TableCell>
                <TableCell>
                  {mapping.created_actor ? (
                    <Link
                      to={`/users/${mapping.created_actor?.email.toLowerCase()}`}
                      sx={{textDecoration: 'none', color: 'inherit'}}
                      component={RouterLink}>
                      {mapping.created_actor?.first_name} {mapping.created_actor?.last_name}
                    </Link>
                  ) : (
                    'System'
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      <TablePagination
        component="div"
        count={activeRoleMappings.length}
        page={page}
        onPageChange={handleChangePage}
        rowsPerPage={rowsPerPage}
        rowsPerPageOptions={[]}
      />
    </Paper>
  );
}
