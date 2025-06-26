import React from 'react';
import {Link as RouterLink} from 'react-router-dom';

import Link from '@mui/material/Link';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';

import dayjs from 'dayjs';

import {OktaUserGroupMember} from '../api/apiSchemas';
import Started from './Started';
import Ending from './Ending';

interface RoleBasedAccessTableProps {
  accessEntries: OktaUserGroupMember[];
}

export default function RoleBasedAccessTable({accessEntries}: RoleBasedAccessTableProps) {
  return (
    <Table size="small" aria-label="role-based access">
      <TableHead>
        <TableRow>
          <TableCell>Role Name</TableCell>
          <TableCell>Access Type</TableCell>
          <TableCell>Role Started</TableCell>
          <TableCell>Role Ending</TableCell>
          <TableCell>Group Access Started</TableCell>
          <TableCell>Group Access Ending</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {accessEntries.map((access) => (
          <TableRow key={access.id}>
            <TableCell>
              <Link
                to={`/roles/${access.role_group_mapping?.role_group?.name}`}
                sx={{textDecoration: 'none', color: 'inherit'}}
                component={RouterLink}>
                {access.role_group_mapping?.role_group?.name}
              </Link>
            </TableCell>
            <TableCell>{access.is_owner ? 'Owner' : 'Member'}</TableCell>
            <TableCell>
              {access.role_group_mapping?.created_at ? (
                <span title={access.role_group_mapping.created_at}>
                  {dayjs(access.role_group_mapping.created_at).startOf('second').fromNow()}
                </span>
              ) : (
                'Unknown'
              )}
            </TableCell>
            <TableCell>
              {access.role_group_mapping?.ended_at ? (
                <span title={access.role_group_mapping.ended_at}>
                  {dayjs(access.role_group_mapping.ended_at).startOf('second').fromNow()}
                </span>
              ) : (
                'Never'
              )}
            </TableCell>
            <TableCell>
              <Started memberships={[access]} />
            </TableCell>
            <TableCell>
              <Ending memberships={[access]} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
