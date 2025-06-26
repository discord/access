import React from 'react';
import {Link as RouterLink} from 'react-router-dom';

import Link from '@mui/material/Link';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TablePagination from '@mui/material/TablePagination';
import Typography from '@mui/material/Typography';

import {OktaUserGroupMember} from '../api/apiSchemas';
import {displayUserName} from '../helpers';
import Started from './Started';
import Ending from './Ending';
import InlineReason from './InlineReason';
import AccessMethodChip from './AccessMethodChip';

interface AccessHistoryTableProps {
  accessEntries: OktaUserGroupMember[];
  highlightColor: 'success' | 'danger';
  showEndedBy: boolean;
  emptyMessage?: string;
}

export default function AccessHistoryTable({
  accessEntries,
  highlightColor,
  showEndedBy,
  emptyMessage,
}: AccessHistoryTableProps) {
  const [page, setPage] = React.useState(0);
  const rowsPerPage = 5;
  const handleChangePage = (event: unknown, newPage: number) => {
    setPage(newPage);
  };

  const columns = [
    'Access Type',
    'Method',
    'Started',
    'Ending',
    showEndedBy ? 'Removed by' : 'Added by',
    'Access Reason',
  ];

  return (
    <>
      <Table size="small" aria-label="access history">
        <TableHead>
          <TableRow>
            {columns.map((col) => (
              <TableCell key={col}>{col}</TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {accessEntries.length === 0 ? (
            <TableRow>
              <TableCell colSpan={columns.length} align="center">
                <Typography variant="body2" color="text.secondary">
                  {emptyMessage}
                </Typography>
              </TableCell>
            </TableRow>
          ) : (
            accessEntries.slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage).map((access) => (
              <TableRow
                key={access.id}
                sx={{
                  backgroundColor: highlightColor,
                }}>
                <TableCell>{access.is_owner ? 'Owner' : 'Member'}</TableCell>
                <TableCell>
                  <AccessMethodChip roleGroupMapping={access.role_group_mapping} />
                </TableCell>
                <TableCell>
                  <Started memberships={[access]} />
                </TableCell>
                <TableCell>
                  <Ending memberships={[access]} />
                </TableCell>
                <TableCell>
                  {showEndedBy ? (
                    access.ended_actor ? (
                      <Link
                        to={`/users/${access.ended_actor?.email.toLowerCase()}`}
                        sx={{textDecoration: 'none', color: 'inherit'}}
                        component={RouterLink}>
                        {displayUserName(access.ended_actor)}
                      </Link>
                    ) : (
                      'System'
                    )
                  ) : access.created_actor ? (
                    <Link
                      to={`/users/${access.created_actor?.email.toLowerCase()}`}
                      sx={{textDecoration: 'none', color: 'inherit'}}
                      component={RouterLink}>
                      {displayUserName(access.created_actor)}
                    </Link>
                  ) : (
                    'System'
                  )}
                </TableCell>
                <TableCell>
                  {access.created_reason ? <InlineReason reason={access.created_reason} /> : <InlineReason />}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
      <TablePagination
        component="div"
        count={accessEntries.length}
        page={page}
        onPageChange={handleChangePage}
        rowsPerPage={rowsPerPage}
        rowsPerPageOptions={[]}
      />
    </>
  );
}
