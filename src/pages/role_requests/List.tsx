import React from 'react';

import {Link as RouterLink, useSearchParams, useNavigate} from 'react-router-dom';
import Link from '@mui/material/Link';
import Button from '@mui/material/Button';

import Paper from '@mui/material/Paper';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TableFooter from '@mui/material/TableFooter';
import TablePagination from '@mui/material/TablePagination';
import Typography from '@mui/material/Typography';
import TextField from '@mui/material/TextField';
import Box from '@mui/material/Box';
import Grid from '@mui/material/Grid';
import Autocomplete from '@mui/material/Autocomplete';
import {SelectChangeEvent} from '@mui/material/Select';

import dayjs from 'dayjs';
import RelativeTime from 'dayjs/plugin/relativeTime';

import {useCurrentUser} from '../../authentication';
import ChangeTitle from '../../tab-title';
import CreateRoleRequest from './Create';
import {useGetRoleRequests} from '../../api/apiComponents';
import {displayUserName, perPage} from '../../helpers';
import TablePaginationActions from '../../components/actions/TablePaginationActions';
import TableTopBar, {TableTopBarAutocomplete} from '../../components/TableTopBar';
import StatusFilter, {StatusFilterValue} from '../../components/StatusFilter';
import {OktaUserGroupMember} from '../../api/apiSchemas';

dayjs.extend(RelativeTime);

function userOwnsRoles(user: OktaUserGroupMember[]): boolean {
  return user.reduce((out, ownership) => {
    return out || ownership.active_group?.type == 'role_group';
  }, false);
}

export default function ListRoleRequests() {
  const currentUser = useCurrentUser();
  const enableCreateRequest = userOwnsRoles(currentUser.active_group_ownerships ?? []);

  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const [requesterRoleId, setRequesterRoleId] = React.useState<string | null>(null);
  const [requesterUserId, setRequesterUserId] = React.useState<string | null>(null);
  const [assigneeUserId, setAssigneeUserId] = React.useState<string | null>(null);
  const [resolverUserId, setResolverUserId] = React.useState<string | null>(null);
  const [statusFilter, setStatusFilter] = React.useState<StatusFilterValue>('ALL');

  const [searchQuery, setSearchQuery] = React.useState<string | null>(null);
  const [searchInput, setSearchInput] = React.useState('');

  const [page, setPage] = React.useState(0);
  const [rowsPerPage, setRowsPerPage] = React.useState(20);

  React.useEffect(() => {
    setRequesterRoleId(searchParams.get('requester_role_id') ?? null);
    setRequesterUserId(searchParams.get('requester_user_id') ?? null);
    setAssigneeUserId(searchParams.get('assignee_user_id') ?? null);
    setResolverUserId(searchParams.get('resolver_user_id') ?? null);
    setStatusFilter((searchParams.get('status') as StatusFilterValue) ?? 'ALL');
    setSearchQuery(searchParams.get('q') ?? null);
    if (searchInput == '') {
      setSearchInput(searchParams.get('q') ?? '');
    }
    setPage(parseInt(searchParams.get('page') ?? '0', 10));
    setRowsPerPage(parseInt(searchParams.get('per_page') ?? '20', 10));
  }, [searchParams]);

  const {data, error, isLoading} = useGetRoleRequests({
    queryParams: Object.assign(
      {page: page, per_page: rowsPerPage},
      searchQuery == null ? null : {q: searchQuery},
      requesterRoleId == null ? null : {requester_role_id: requesterRoleId},
      requesterUserId == null ? null : {requester_user_id: requesterUserId},
      assigneeUserId == null ? null : {assignee_user_id: assigneeUserId},
      resolverUserId == null ? null : {resolver_user_id: resolverUserId},
      statusFilter === 'ALL' ? null : {status: statusFilter},
    ),
  });

  const {data: searchData} = useGetRoleRequests({
    queryParams: {page: 0, per_page: 10, q: searchInput},
  });

  const rows = data?.results ?? [];
  const totalRows = data?.total ?? 0;

  // If there's only one search result, just redirect to that request page
  if (searchQuery != null && totalRows == 1) {
    navigate('/role-requests/' + rows[0].id, {
      replace: true,
    });
  }

  // Avoid a layout jump when reaching the last page with empty rows.
  const emptyRows = rowsPerPage - rows.length;

  const searchRows = searchData?.results ?? [];

  const handleChangePage = (event: React.MouseEvent<HTMLButtonElement> | null, newPage: number) => {
    setSearchParams((params) => {
      params.set('page', newPage.toString(10));
      return params;
    });
    setPage(newPage);
  };

  const handleChangeRowsPerPage = (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    setSearchParams((params) => {
      params.set('page', '0');
      params.set('per_page', event.target.value);
      return params;
    });
    setRowsPerPage(parseInt(event.target.value, 10));
    setPage(0);
  };

  const handleSearchSubmit = (event: React.SyntheticEvent, newValue: string | null) => {
    if (newValue == null) {
      setSearchParams((params) => {
        params.delete('q');
        return params;
      });
    } else {
      const requestId = newValue.split(';')[0];
      setSearchParams((params) => {
        params.set('page', '0');
        params.set('q', requestId);
        return params;
      });
      setPage(0);
    }
    setSearchQuery(newValue);
  };

  const handleStatusFilter = (event: SelectChangeEvent<StatusFilterValue>) => {
    const newValue = event.target.value as StatusFilterValue;
    if (newValue === 'ALL') {
      setSearchParams((params) => {
        params.delete('status');
        params.set('page', '0');
        return params;
      });
    } else {
      setSearchParams((params) => {
        params.set('page', '0');
        params.set('status', newValue);
        return params;
      });
    }
    setPage(0);
    setStatusFilter(newValue);
  };

  return (
    <>
      <ChangeTitle title="Role Requests" />
      <TableContainer component={Paper}>
        <TableTopBar title="Role Requests">
          <CreateRoleRequest currentUser={currentUser} enabled={enableCreateRequest}></CreateRoleRequest>
          <StatusFilter value={statusFilter} onChange={handleStatusFilter} />
          <TableTopBarAutocomplete
            options={searchRows.map(
              (row) =>
                row.id +
                ';' +
                displayUserName(row.requester) +
                ';' +
                row.request_ownership +
                ';' +
                (row.requested_group?.name ?? '') +
                ';' +
                (row.status ?? '') +
                ';' +
                displayUserName(row.resolver),
            )}
            onChange={handleSearchSubmit}
            onInputChange={(event, newInputValue) => {
              setSearchInput(newInputValue?.split(';')[0] ?? '');
            }}
            defaultValue={searchQuery}
            renderOption={(props, option, state) => {
              const [id, displayName, ownership, group, status, resolver] = option.split(';');
              return (
                <li key={id} {...props}>
                  <Grid container alignItems="center">
                    <Grid item>
                      <Box>
                        {displayName} {ownership == 'true' ? 'ownership of' : 'membership to'} {group}
                      </Box>
                      <Typography variant="body2" color="text.secondary">
                        {status} {status == 'PENDING' || resolver == '' ? '' : 'by ' + resolver}
                      </Typography>
                    </Grid>
                  </Grid>
                </li>
              );
            }}
          />
        </TableTopBar>
        <Table sx={{minWidth: 650}} size="small" aria-label="roles">
          <TableHead>
            <TableRow>
              <TableCell>Requester Role</TableCell>
              <TableCell>Requester</TableCell>
              <TableCell>Request</TableCell>
              <TableCell>Resolver</TableCell>
              <TableCell>Status</TableCell>
              <TableCell colSpan={2}>Created</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((row) => (
              <TableRow
                key={row.id}
                sx={{
                  bgcolor: ({palette: {highlight}}) =>
                    row.status == 'APPROVED'
                      ? highlight.success.main
                      : row.status == 'REJECTED'
                        ? highlight.danger.main
                        : 'inherit',
                }}>
                <TableCell>
                  {(row.requester_role?.deleted_at ?? null) != null ? (
                    <Link
                      to={`/groups/${row.requester_role?.id ?? ''}`}
                      sx={{textDecoration: 'line-through', color: 'inherit'}}
                      component={RouterLink}>
                      {row.requester_role?.name ?? ''}
                    </Link>
                  ) : (
                    <Link
                      to={`/groups/${row.requester_role?.name ?? ''}`}
                      sx={{textDecoration: 'none', color: 'inherit'}}
                      component={RouterLink}>
                      {row.requester_role?.name ?? ''}
                    </Link>
                  )}
                </TableCell>
                <TableCell>
                  {(row.requester?.deleted_at ?? null) != null ? (
                    <Link
                      to={`/users/${row.requester?.id ?? ''}`}
                      sx={{textDecoration: 'line-through', color: 'inherit'}}
                      component={RouterLink}>
                      {displayUserName(row.requester)}
                    </Link>
                  ) : (
                    <Link
                      to={`/users/${row.requester?.email.toLowerCase() ?? ''}`}
                      sx={{textDecoration: 'none', color: 'inherit'}}
                      component={RouterLink}>
                      {displayUserName(row.requester)}
                    </Link>
                  )}
                </TableCell>
                <TableCell>
                  {row.request_ownership ? 'Ownership of ' : 'Membership to '}
                  {(row.requested_group?.deleted_at ?? null) != null ? (
                    <Link
                      to={`/groups/${row.requested_group?.id ?? ''}`}
                      sx={{textDecoration: 'line-through', color: 'inherit'}}
                      component={RouterLink}>
                      {row.requested_group?.name ?? ''}
                    </Link>
                  ) : (
                    <Link
                      to={`/groups/${row.requested_group?.name ?? ''}`}
                      sx={{textDecoration: 'none', color: 'inherit'}}
                      component={RouterLink}>
                      {row.requested_group?.name ?? ''}
                    </Link>
                  )}
                </TableCell>
                <TableCell>
                  {row.resolver == null && row.status != 'PENDING' ? (
                    'Access'
                  ) : (row.resolver?.deleted_at ?? null) != null ? (
                    <Link
                      to={`/users/${row.resolver?.id ?? ''}`}
                      sx={{textDecoration: 'line-through', color: 'inherit'}}
                      component={RouterLink}>
                      {displayUserName(row.resolver)}
                    </Link>
                  ) : (
                    <Link
                      to={`/users/${row.resolver?.email.toLowerCase() ?? ''}`}
                      sx={{textDecoration: 'none', color: 'inherit'}}
                      component={RouterLink}>
                      {displayUserName(row.resolver)}
                    </Link>
                  )}
                </TableCell>
                <TableCell>
                  <Link
                    to={`/role-requests/${row.id}`}
                    sx={{textDecoration: 'none', color: 'inherit'}}
                    component={RouterLink}>
                    {row.status}
                  </Link>
                </TableCell>
                <TableCell>
                  <Link
                    to={`/role-requests/${row.id}`}
                    sx={{textDecoration: 'none', color: 'inherit'}}
                    component={RouterLink}>
                    <span title={row.created_at}>{dayjs(row.created_at).startOf('second').fromNow()}</span>
                  </Link>
                </TableCell>
                <TableCell>
                  <Button variant="contained" size="small" to={`/role-requests/${row.id}`} component={RouterLink}>
                    View
                  </Button>
                </TableCell>
              </TableRow>
            ))}
            {emptyRows > 0 && (
              <TableRow style={{height: 33 * emptyRows}}>
                <TableCell colSpan={6} />
              </TableRow>
            )}
          </TableBody>
          <TableFooter>
            <TableRow>
              <TablePagination
                rowsPerPageOptions={perPage}
                colSpan={6}
                count={totalRows}
                rowsPerPage={rowsPerPage}
                page={page}
                SelectProps={{
                  inputProps: {
                    'aria-label': 'rows per page',
                  },
                  native: true,
                }}
                onPageChange={handleChangePage}
                onRowsPerPageChange={handleChangeRowsPerPage}
                ActionsComponent={TablePaginationActions}
              />
            </TableRow>
          </TableFooter>
        </Table>
      </TableContainer>
    </>
  );
}
