import React from 'react';

import {Link as RouterLink, useParams, useSearchParams, useNavigate} from 'react-router-dom';
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
import TableSortLabel from '@mui/material/TableSortLabel';
import Typography from '@mui/material/Typography';
import Chip from '@mui/material/Chip';
import TextField from '@mui/material/TextField';
import Autocomplete from '@mui/material/Autocomplete';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';
import ToggleButton from '@mui/material/ToggleButton';

import dayjs from 'dayjs';

import {displayGroupType, displayUserName} from '../../helpers';
import ChangeTitle from '../../tab-title';
import {useGetUserById, useGetUserGroupAudits, useGetGroups} from '../../api/apiComponents';
import {OktaUser} from '../../api/apiSchemas';
import NotFound from '../NotFound';
import CreatedReason from '../../components/CreatedReason';
import Loading from '../../components/Loading';
import Started from '../../components/Started';
import Ending from '../../components/Ending';
import TablePaginationActions from '../../components/actions/TablePaginationActions';
import TableTopBar, {TableTopBarAutocomplete} from '../../components/TableTopBar';

type OrderBy = 'moniker' | 'created_at' | 'ended_at';
type OrderDirection = 'asc' | 'desc';

export default function AuditUser() {
  const {id} = useParams();
  const navigate = useNavigate();

  const [searchParams, setSearchParams] = useSearchParams();

  const [orderBy, setOrderBy] = React.useState<OrderBy>('created_at');
  const [orderDirection, setOrderDirection] = React.useState<OrderDirection>('desc');
  const [searchQuery, setSearchQuery] = React.useState<string | null>(null);
  const [searchInput, setSearchInput] = React.useState('');

  const [page, setPage] = React.useState(0);
  const [rowsPerPage, setRowsPerPage] = React.useState(20);

  const [filterActive, setFilterActive] = React.useState<boolean | null>();
  const [filterOwner, setFilterOwner] = React.useState<boolean | null>();

  React.useEffect(() => {
    setOrderBy((searchParams.get('order_by') as OrderBy) ?? 'created_at');
    setOrderDirection((searchParams.get('order_desc') ?? 'true') === 'true' ? 'desc' : 'asc');
    setSearchQuery(searchParams.get('q') ?? null);
    if (searchInput == '') {
      setSearchInput(searchParams.get('q') ?? '');
    }
    setFilterActive(searchParams.get('active') == null ? null : searchParams.get('active') == 'true');
    setFilterOwner(searchParams.get('owner') == null ? null : searchParams.get('owner') == 'true');
    setPage(parseInt(searchParams.get('page') ?? '0', 10));
    setRowsPerPage(parseInt(searchParams.get('per_page') ?? '20', 10));
  }, [searchParams]);

  const {
    data: userData,
    isError,
    isLoading: userIsLoading,
  } = useGetUserById({
    pathParams: {userId: id ?? ''},
  });

  const {
    data,
    error,
    isLoading: userAuditIsLoading,
  } = useGetUserGroupAudits({
    queryParams: Object.assign(
      {user_id: id ?? '', page: page, per_page: rowsPerPage},
      orderBy == null ? null : {order_by: orderBy},
      orderDirection == null ? null : {order_desc: orderDirection == 'desc' ? 'true' : 'false'},
      searchQuery == null ? null : {q: searchQuery},
      filterActive == null ? null : {active: filterActive},
      filterOwner == null ? null : {owner: filterOwner},
    ),
  });

  const {data: searchData} = useGetGroups({
    queryParams: {page: 0, per_page: 10, q: searchInput},
  });

  if (isError) {
    return <NotFound />;
  }

  if (userIsLoading || userAuditIsLoading) {
    return <Loading />;
  }

  const user = userData ?? ({} as OktaUser);

  const rows = data?.results ?? [];
  const totalRows = data?.total ?? 0;

  // Avoid a layout jump when reaching the last page with empty rows.
  const emptyRows = rowsPerPage - rows.length;

  const searchRows = searchData?.results ?? [];

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
      setSearchParams((params) => {
        params.set('page', '0');
        params.set('q', newValue);
        return params;
      });
      setPage(0);
    }
    setSearchQuery(newValue);
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
        return params;
      });
    }
    setFilterOwner(newValue);
  };

  const handleOwnerOrMember = (event: React.MouseEvent<HTMLElement>, newValue: boolean | null) => {
    console.log(newValue);
    if (newValue == null) {
      setSearchParams((params) => {
        params.delete('owner');
        return params;
      });
    } else {
      setSearchParams((params) => {
        params.set('owner', newValue ? 'true' : 'false');
        return params;
      });
    }
    setFilterOwner(newValue);
  };

  return (
    <>
      <ChangeTitle title={`${displayUserName(user)} Audit`} />
      <TableContainer component={Paper}>
        <TableTopBar
          title={`User Audit: ${displayUserName(user)}`}
          link={user.deleted_at != null ? `/users/${user.id}` : `/users/${user.email.toLowerCase()}`}>
          <ToggleButtonGroup size="small" exclusive value={filterOwner} onChange={handleOwnerOrMember}>
            <ToggleButton value={false}>Member</ToggleButton>
            <ToggleButton value={true}>Owner</ToggleButton>
          </ToggleButtonGroup>
          <ToggleButtonGroup size="small" exclusive value={filterActive} onChange={handleActiveOrInactive}>
            <ToggleButton value={true}>Active</ToggleButton>
            <ToggleButton value={false}>Inactive</ToggleButton>
          </ToggleButtonGroup>
          <TableTopBarAutocomplete
            options={searchRows.map((row) => row.name)}
            onChange={handleSearchSubmit}
            onInputChange={(event, newInputValue) => setSearchInput(newInputValue)}
            defaultValue={searchQuery}
          />
        </TableTopBar>
        <Table sx={{minWidth: 650}} size="small" aria-label="groups">
          <TableHead>
            <TableRow>
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
              <TableCell>Direct or via Role</TableCell>
              <TableCell>
                <TableSortLabel
                  active={orderBy === 'created_at'}
                  direction={orderBy === 'created_at' ? orderDirection : 'desc'}
                  onClick={handleSortChange('created_at')}>
                  Started
                </TableSortLabel>
              </TableCell>
              <TableCell>Added by</TableCell>
              <TableCell>
                <TableSortLabel
                  active={orderBy === 'ended_at'}
                  direction={orderBy === 'ended_at' ? orderDirection : 'desc'}
                  onClick={handleSortChange('ended_at')}>
                  Ending
                </TableSortLabel>
              </TableCell>
              <TableCell>Removed by</TableCell>
              <TableCell align="center">Access Request</TableCell>
              <TableCell align="center">Justification</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((row) => (
              <TableRow
                key={row.id}
                sx={{
                  bgcolor: ({palette: {highlight}}) =>
                    row.ended_at == null || dayjs().isBefore(dayjs(row.ended_at))
                      ? highlight.success.main
                      : highlight.danger.main,
                }}>
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
                  {(row.group?.deleted_at ?? null) != null ? (
                    displayGroupType(row.group)
                  ) : (
                    <Link
                      to={`/groups/${row.group?.name ?? ''}`}
                      sx={{textDecoration: 'none', color: 'inherit'}}
                      component={RouterLink}>
                      {displayGroupType(row.group)}
                    </Link>
                  )}
                </TableCell>
                <TableCell>{row.is_owner ? 'Owner' : 'Member'}</TableCell>
                <TableCell>
                  {row.role_group_mapping == null ? (
                    <Chip key="direct" label="Direct" color="primary" />
                  ) : (
                    <Chip
                      label={row.role_group_mapping?.role_group?.name}
                      color="primary"
                      onClick={() => navigate(`/roles/${row.role_group_mapping?.role_group?.name}`)}
                    />
                  )}
                </TableCell>
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
                <TableCell>
                  {row.ended_at != null && dayjs().isAfter(dayjs(row.ended_at)) ? (
                    (row.ended_actor?.deleted_at ?? null) != null ? (
                      <Link
                        to={`/users/${row.ended_actor?.id ?? ''}`}
                        sx={{textDecoration: 'line-through', color: 'inherit'}}
                        component={RouterLink}>
                        {displayUserName(row.ended_actor)}
                      </Link>
                    ) : (
                      <Link
                        to={`/users/${(row.ended_actor?.email ?? '').toLowerCase()}`}
                        sx={{textDecoration: 'none', color: 'inherit'}}
                        component={RouterLink}>
                        {displayUserName(row.ended_actor)}
                      </Link>
                    )
                  ) : (
                    ''
                  )}
                </TableCell>
                <TableCell align="center">
                  {row.access_request != null ? (
                    <Button
                      variant="contained"
                      size="small"
                      to={`/requests/${row.access_request?.id ?? ''}`}
                      component={RouterLink}>
                      View
                    </Button>
                  ) : null}
                </TableCell>
                <TableCell align="center">
                  {row.created_reason ? <CreatedReason created_reason={row.created_reason} /> : null}
                </TableCell>
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
                rowsPerPageOptions={[5, 10, 20]}
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
