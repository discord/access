import React from 'react';
import {Link as RouterLink} from 'react-router-dom';

import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Chip from '@mui/material/Chip';
import Divider from '@mui/material/Divider';
import Link from '@mui/material/Link';
import Paper from '@mui/material/Paper';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Typography from '@mui/material/Typography';

import {displayUserName} from '../helpers';
import {ACCESS_APP_RESERVED_NAME} from '../authorization';
import {EmptyListEntry} from './EmptyListEntry';
import {OktaUserSummary, RequestReviewers as RequestReviewersData} from '../api/apiSchemas';

type OwnerLevel = RequestReviewersData['owner_levels'][number]['owner_level'];

interface RequestReviewersProps {
  reviewers: RequestReviewersData | undefined;
  // Names the group_owners level.
  groupName?: string | null;
  // Names the app_owners level.
  appName?: string | null;
}

function levelTitle(level: OwnerLevel, groupName?: string | null, appName?: string | null): string {
  switch (level) {
    case 'group_owners':
      return `${groupName ?? 'Group'} Owners`;
    case 'app_owners':
      return `${appName ?? 'App'} App Owners`;
    case 'access_admins':
      return `${ACCESS_APP_RESERVED_NAME} Admins`;
  }
}

function sortByName(a: OktaUserSummary, b: OktaUserSummary): number {
  return displayUserName(a).localeCompare(displayUserName(b));
}

// A pending request's possible reviewers, one table per owner level nearest first,
// with the level the request is currently assigned to marked.
export default function RequestReviewers({reviewers, groupName, appName}: RequestReviewersProps) {
  if (reviewers == undefined) {
    return null;
  }

  return (
    <Box sx={{my: 2}}>
      <Paper sx={{p: 2}}>
        <Typography variant="body1">
          {reviewers.assigned_owner_level == null ? (
            <>
              Request is <b>pending</b>. Its owners at each level are listed below, in order.
            </>
          ) : (
            <>
              Request is <b>pending</b>. It's assigned to the owners marked below; if you need to escalate, the
              remaining owners are listed in order.
            </>
          )}
        </Typography>
      </Paper>
      {reviewers.assigned_owner_level == null ? (
        <Alert severity="warning" sx={{mt: 1}}>
          No one is currently eligible to review this request.
        </Alert>
      ) : null}
      {reviewers.owner_levels.map(({owner_level, reviewers: users}) => (
        <Paper key={owner_level} sx={{p: 2, mt: 1}}>
          <Table size="small" aria-label={owner_level}>
            <TableHead>
              <TableRow>
                <TableCell colSpan={3}>
                  <Box sx={{display: 'flex', alignItems: 'center', gap: 1}}>
                    <Typography variant="h6" color="text.accent">
                      {levelTitle(owner_level, groupName, appName)}
                    </Typography>
                    {owner_level === reviewers.assigned_owner_level ? (
                      <Chip label="Assigned" size="small" color="primary" />
                    ) : null}
                  </Box>
                </TableCell>
              </TableRow>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell>Email</TableCell>
                <TableCell>
                  <Box sx={{display: 'flex', justifyContent: 'flex-end', alignItems: 'right'}}>
                    <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                    Total Owners: {users.length}
                  </Box>
                </TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {users.length > 0 ? (
                [...users].sort(sortByName).map((user) => (
                  <TableRow key={user.id}>
                    <TableCell>
                      <Link
                        to={`/users/${user.email.toLowerCase()}`}
                        sx={{textDecoration: 'none', color: 'inherit'}}
                        component={RouterLink}>
                        {displayUserName(user)}
                      </Link>
                    </TableCell>
                    <TableCell colSpan={2}>
                      <Link
                        to={`/users/${user.email.toLowerCase()}`}
                        sx={{textDecoration: 'none', color: 'inherit'}}
                        component={RouterLink}>
                        {user.email.toLowerCase()}
                      </Link>
                    </TableCell>
                  </TableRow>
                ))
              ) : (
                <EmptyListEntry cellProps={{colSpan: 3}} />
              )}
            </TableBody>
          </Table>
        </Paper>
      ))}
    </Box>
  );
}
