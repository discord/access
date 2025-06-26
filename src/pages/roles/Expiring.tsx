import React from 'react';
import {Link as RouterLink, useSearchParams} from 'react-router-dom';

import Autocomplete from '@mui/material/Autocomplete';
import Box from '@mui/material/Box';
import Chip from '@mui/material/Chip';
import Link from '@mui/material/Link';
import Paper from '@mui/material/Paper';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableFooter from '@mui/material/TableFooter';
import TableHead from '@mui/material/TableHead';
import TablePagination from '@mui/material/TablePagination';
import TableRow from '@mui/material/TableRow';
import TableSortLabel from '@mui/material/TableSortLabel';
import TextField from '@mui/material/TextField';
import ToggleButton from '@mui/material/ToggleButton';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';

import dayjs, {Dayjs} from 'dayjs';

import BulkRenewal from './BulkRenewal';
import NotFound from '../NotFound';
import CreateRoleRequest from '../role_requests/Create';
import {useGetGroupRoleAudits, useGetGroups} from '../../api/apiComponents';
import ChangeTitle from '../../tab-title';
import {useCurrentUser} from '../../authentication';
import {canManageGroup} from '../../authorization';
import DateRangePicker from '../../components/DateRange';
import Ending from '../../components/Ending';
import Loading from '../../components/Loading';
import Started from '../../components/Started';
import TablePaginationActions from '../../components/actions/TablePaginationActions';
import {displayUserName, perPage} from '../../helpers';
import TableTopBar, {TableTopBarAutocomplete} from '../../components/TableTopBar';

type OrderBy = 'moniker' | 'ended_at';
type OrderDirection = 'asc' | 'desc';

export default function ExpiringRoless() {
  const currentUser = useCurrentUser();

  const [orderBy, setOrderBy] = React.useState<OrderBy>('ended_at');
  const [orderDirection, setOrderDirection] = React.useState<OrderDirection>('asc');
  const [searchParams, setSearchParams] = useSearchParams();
  const [ownerId, setOwnerId] = React.useState<string | null>(null);
  const [roleOwnerId, setRoleOwnerId] = React.useState<string | null>(null);
  const [searchQuery, setSearchQuery] = React.useState<string | null>(null);
  const [searchInput, setSearchInput] = React.useState('');
  const [page, setPage] = React.useState(0);
  const [rowsPerPage, setRowsPerPage] = React.useState(20);
  const [filterNeedsReview, setFilterNeedsReview] = React.useState<boolean>(true);
  const [filterActive, setFilterActive] = React.useState<boolean | null>(true);
  const [filterAppOwnership, setFilterAppOwnership] = React.useState<boolean>(false);
  const [startDate, setStartDate] = React.useState<Dayjs | null>(dayjs());
  const [endDate, setEndDate] = React.useState<Dayjs | null>(dayjs().add(30, 'day'));
  const [datesPicked, setDatesPicked] = React.useState(0);

  React.useEffect(() => {
    setOrderBy((searchParams.get('order_by') as OrderBy) ?? 'ended_at');
    setOrderDirection((searchParams.get('order_desc') ?? 'true') === 'true' ? 'asc' : 'desc');
    setOwnerId(searchParams.get('owner_id') ?? null);
    setRoleOwnerId(searchParams.get('role_owner_id') ?? null);
    setSearchQuery(searchParams.get('q') ?? null);
    if (searchInput == '') {
      setSearchInput(searchParams.get('q') ?? '');
    }
    setPage(parseInt(searchParams.get('page') ?? '0', 10));
    setRowsPerPage(parseInt(searchParams.get('per_page') ?? '20', 10));
    setFilterNeedsReview(searchParams.get('needs_review') !== 'false');
    setFilterActive(searchParams.get('active') == null ? null : searchParams.get('active') == 'true');
    setFilterAppOwnership(searchParams.get('app_owner') == 'true');
    setStartDate(searchParams.get('start_date') == null ? dayjs() : dayjs.unix(Number(searchParams.get('start_date'))));
    setEndDate(
      searchParams.get('end_date') == null ? dayjs().add(30, 'day') : dayjs.unix(Number(searchParams.get('end_date'))),
    );
  }, [searchParams]);

  const {
    data,
    isError,
    isLoading: expiringGroupsIsLoading,
  } = useGetGroupRoleAudits({
    queryParams: Object.assign(
      {page: page, per_page: rowsPerPage},
      orderBy == null ? null : {order_by: orderBy},
      orderDirection == null ? null : {order_desc: orderDirection == 'desc' ? 'true' : 'false'},
      searchQuery == null ? null : {q: searchQuery},
      ownerId == null ? null : {owner_id: ownerId},
      filterNeedsReview == null ? null : {needs_review: filterNeedsReview},
      roleOwnerId == null ? null : {role_owner_id: roleOwnerId},
      filterActive == null ? null : {active: filterActive},
      {app_owner: filterAppOwnership},
      startDate == null ? null : {start_date: startDate.unix()},
      endDate == null ? null : {end_date: endDate.unix()},
    ),
  });

  const {data: searchData} = useGetGroups({
    queryParams: {page: 0, per_page: 10, q: searchInput},
  });

  if (isError) {
    return <NotFound />;
  }

  if (expiringGroupsIsLoading) {
    return <Loading />;
  }

  const rows = data?.results ?? [];
  const totalRows = data?.total ?? 0;

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

  const handleSortChange = (property: OrderBy) => (event: React.MouseEvent<unknown>) => {
    const isAsc = orderBy === property && orderDirection === 'asc';
    setSearchParams((params) => {
      params.set('order_by', property);
      params.set('order_desc', isAsc ? 'true' : 'false');
      return params;
    });
    setOrderDirection(isAsc ? 'desc' : 'asc');
    setOrderBy(property);
  };

  const handleSearchSubmit = (event: React.SyntheticEvent, newValue: string | null) => {
    if (newValue == null) {
      setSearchParams((params) => {
        params.delete('q');
        return params;
      });
    } else {
      setSearchParams((params) => {
        params.set('page', '0');
        params.set('q', newValue);
        return params;
      });
      setPage(0);
    }
    setSearchQuery(newValue);
  };

  const handleNeedsReviewOrAll = (event: React.MouseEvent<HTMLElement>, newValue: boolean) => {
    if (newValue !== null) {
      setSearchParams((params) => {
        params.set('needs_review', newValue ? 'true' : 'false');
        params.set('page', '0');
        return params;
      });
      setPage(0);
    }
  };

  const handleActiveOrInactive = (event: React.MouseEvent<HTMLElement>, newValue: boolean | null) => {
    if (newValue == null) {
      setSearchParams((params) => {
        params.delete('active');
        return params;
      });
    } else {
      setSearchParams((params) => {
        params.set('active', newValue ? 'true' : 'false');
        params.set('page', '0');
        return params;
      });
      setPage(0);
    }
  };

  const handleDirectOrViaAppOwnership = (event: React.MouseEvent<HTMLElement>, newValue: boolean | null) => {
    setSearchParams((params) => {
      params.set('app_owner', newValue ? 'true' : 'false');
      params.set('page', '0');
      return params;
    });
    setPage(0);
  };

  const handleSetStartDate = (newValue: Dayjs | null) => {
    if (newValue == null) {
      setSearchParams((params) => {
        params.delete('start_date');
        return params;
      });
    } else {
      setSearchParams((params) => {
        params.set('start_date', newValue.unix().toString());
        return params;
      });
    }
  };

  const handleSetEndDate = (newValue: Dayjs | null) => {
    if (newValue == null) {
      setSearchParams((params) => {
        params.delete('end_date');
        return params;
      });
    } else {
      setSearchParams((params) => {
        params.set('end_date', newValue.unix().toString());
        return params;
      });
    }
  };

  return (
    <>
      <ChangeTitle title="Expiring Roles" />
      <TableContainer component={Paper}>
        <TableTopBar title="Expiring Roles">
          <BulkRenewal rows={rows.filter((row) => canManageGroup(currentUser, row.group))} />
          <Tooltip title="Show access that still needs review or all expiring access.">
            <ToggleButtonGroup size="small" exclusive value={filterNeedsReview} onChange={handleNeedsReviewOrAll}>
              <ToggleButton value={true}>Pending</ToggleButton>
              <ToggleButton value={false}>All</ToggleButton>
            </ToggleButtonGroup>
          </Tooltip>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={filterActive}
            onChange={handleActiveOrInactive}
            defaultValue={'true'}>
            <ToggleButton value={true}>Active</ToggleButton>
            <ToggleButton value={false}>Inactive</ToggleButton>
          </ToggleButtonGroup>
          {ownerId ? (
            <ToggleButtonGroup
              size="small"
              exclusive
              value={filterAppOwnership}
              onChange={handleDirectOrViaAppOwnership}
              defaultValue={'false'}>
              <ToggleButton value={false}>
                <Tooltip title="Includes all groups directly owned and owned via app ownership where there are no direct group owners">
                  <span>Default Owner</span>
                </Tooltip>
              </ToggleButton>
              <ToggleButton value={true}>
                <Tooltip title="All groups owned directly and via app ownership">
                  <span>All Owned</span>
                </Tooltip>
              </ToggleButton>
            </ToggleButtonGroup>
          ) : null}
          <DateRangePicker
            startDate={startDate}
            setStartDate={handleSetStartDate}
            endDate={endDate}
            setEndDate={handleSetEndDate}
            datesPicked={datesPicked}
            setDatesPicked={setDatesPicked}
            slots={{
              textField: (textFieldProps) => <TextField {...textFieldProps} />,
            }}
          />
          <TableTopBarAutocomplete
            options={searchRows.map((row) => row.name)}
            onChange={handleSearchSubmit}
            onInputChange={(event, newInputValue) => setSearchInput(newInputValue)}
            defaultValue={searchQuery}
          />
        </TableTopBar>
        <Table sx={{minWidth: 650}} size="small" aria-label="roles">
          <TableHead>
            <TableRow>
              <TableCell>Role Name</TableCell>
              <TableCell>
                <TableSortLabel
                  active={orderBy === 'moniker'}
                  direction={orderBy === 'moniker' ? orderDirection : 'desc'}
                  onClick={handleSortChange('moniker')}>
                  Group Name
                </TableSortLabel>
              </TableCell>
              <TableCell>Group Type</TableCell>
              <TableCell>Member or Owner</TableCell>
              <TableCell>
                <TableSortLabel>Started</TableSortLabel>
              </TableCell>
              <TableCell>Added by</TableCell>
              <TableCell>
                <TableSortLabel
                  active={orderBy === 'ended_at'}
                  direction={orderBy === 'ended_at' ? orderDirection : 'asc'}
                  onClick={handleSortChange('ended_at')}>
                  Ending
                </TableSortLabel>
              </TableCell>
              <TableCell>Notes</TableCell>
              <TableCell></TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((row) => (
              <TableRow
                key={row.id}
                sx={{
                  bgcolor: ({palette: {highlight}}) =>
                    dayjs(row.ended_at).isBefore(dayjs())
                      ? highlight.danger.main
                      : row.should_expire
                        ? highlight.info.main
                        : dayjs(row.ended_at).isAfter(dayjs()) && dayjs(row.ended_at).isBefore(dayjs().add(7, 'day'))
                          ? highlight.warning.main
                          : null,
                }}>
                <TableCell>
                  {(row.group?.deleted_at ?? null) != null ? (
                    <Link
                      to={`/roles/${row.role_group?.id ?? ''}`}
                      sx={{textDecoration: 'line-through', color: 'inherit'}}
                      component={RouterLink}>
                      {row.role_group?.name ?? ''}
                    </Link>
                  ) : (
                    <Link
                      to={`/roles/${row.role_group?.name ?? ''}`}
                      sx={{textDecoration: 'none', color: 'inherit'}}
                      component={RouterLink}>
                      {row.role_group?.name ?? ''}
                    </Link>
                  )}
                </TableCell>
                <TableCell>
                  {(row.group?.deleted_at ?? null) != null ? (
                    <Link
                      to={`/groups/${row.group?.id ?? ''}`}
                      sx={{textDecoration: 'line-through', color: 'inherit'}}
                      component={RouterLink}>
                      {row.group?.name ?? ''}
                    </Link>
                  ) : (
                    <Link
                      to={`/groups/${row.group?.name ?? ''}`}
                      sx={{textDecoration: 'none', color: 'inherit'}}
                      component={RouterLink}>
                      {row.group?.name ?? ''}
                    </Link>
                  )}
                </TableCell>
                <TableCell>
                  {row.group?.type == 'okta_group'
                    ? 'Group'
                    : row.group?.type == 'app_group'
                      ? 'App Group'
                      : 'Role Group'}
                </TableCell>
                <TableCell>{row.is_owner ? 'Owner' : 'Member'}</TableCell>
                <TableCell>
                  <Started memberships={[row]} />
                </TableCell>
                <TableCell>
                  {(row.created_actor?.deleted_at ?? null) != null ? (
                    <Link
                      to={`/users/${row.created_actor?.id ?? ''}`}
                      sx={{textDecoration: 'line-through', color: 'inherit'}}
                      component={RouterLink}>
                      {displayUserName(row.created_actor)}
                    </Link>
                  ) : (
                    <Link
                      to={`/users/${(row.created_actor?.email ?? '').toLowerCase()}`}
                      sx={{textDecoration: 'none', color: 'inherit'}}
                      component={RouterLink}>
                      {displayUserName(row.created_actor)}
                    </Link>
                  )}
                </TableCell>
                <TableCell>
                  <Ending memberships={[row]} />
                </TableCell>
                <TableCell>{row.should_expire && 'Reviewed, not renewed'}</TableCell>
                {ownerId == '@me' || canManageGroup(currentUser, row.group) ? (
                  <TableCell align="center">
                    <BulkRenewal
                      rows={rows.filter((row) => canManageGroup(currentUser, row.group))}
                      select={row.id}
                      rereview={row.should_expire}
                    />
                  </TableCell>
                ) : roleOwnerId || canManageGroup(currentUser, row.role_group) ? (
                  <TableCell align="center">
                    <CreateRoleRequest
                      currentUser={currentUser}
                      enabled
                      role={row.role_group}
                      group={row.group}
                      owner={row.is_owner}
                      renew></CreateRoleRequest>
                  </TableCell>
                ) : (
                  <TableCell></TableCell>
                )}
              </TableRow>
            ))}
            {emptyRows > 0 && (
              <TableRow style={{height: 33 * emptyRows}}>
                <TableCell colSpan={9} />
              </TableRow>
            )}
          </TableBody>
          <TableFooter>
            <TableRow>
              <TablePagination
                rowsPerPageOptions={perPage}
                colSpan={9}
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
