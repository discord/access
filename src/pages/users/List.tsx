import React from 'react';

import {Link as RouterLink, useSearchParams, useNavigate} from 'react-router-dom';
import Link from '@mui/material/Link';

import Paper from '@mui/material/Paper';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TableFooter from '@mui/material/TableFooter';
import TablePagination from '@mui/material/TablePagination';
import TextField from '@mui/material/TextField';
import Autocomplete from '@mui/material/Autocomplete';
import Grid from '@mui/material/Grid';
import Typography from '@mui/material/Typography';
import Box from '@mui/material/Box';

import {useGetUsers} from '../../api/apiComponents';
import TablePaginationActions from '../../components/actions/TablePaginationActions';
import UserAvatar from './UserAvatar';
import {displayUserName, perPage} from '../../helpers';
import {Stack} from '@mui/material';
import TableTopBar, {renderUserOption, TableTopBarAutocomplete} from '../../components/TableTopBar';

export default function ListUsers() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const [searchQuery, setSearchQuery] = React.useState<string | null>(null);
  const [searchInput, setSearchInput] = React.useState('');

  const [page, setPage] = React.useState(0);
  const [rowsPerPage, setRowsPerPage] = React.useState(20);

  React.useEffect(() => {
    setSearchQuery(searchParams.get('q') ?? null);
    if (searchInput == '') {
      setSearchInput(searchParams.get('q') ?? '');
    }
    setPage(parseInt(searchParams.get('page') ?? '0', 10));
    setRowsPerPage(parseInt(searchParams.get('per_page') ?? '20', 10));
  }, [searchParams]);

  const {data, error, isLoading} = useGetUsers({
    queryParams: Object.assign({page: page, per_page: rowsPerPage}, searchQuery == null ? null : {q: searchQuery}),
  });

  const {data: searchData} = useGetUsers({
    queryParams: {page: 0, per_page: 10, q: searchInput},
  });

  const rows = data?.results ?? [];
  const totalRows = data?.total ?? 0;

  // If there's only one search result, just redirect to that user's page
  if (searchQuery != null && totalRows == 1) {
    navigate('/users/' + rows[0].email.toLowerCase(), {
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
      const displayName = newValue.split(';')[0];
      setSearchParams((params) => {
        params.set('page', '0');
        params.set('q', displayName);
        return params;
      });
      setPage(0);
    }
    setSearchQuery(newValue);
  };

  return (
    <TableContainer component={Paper}>
      <TableTopBar title="Users">
        <TableTopBarAutocomplete
          options={searchRows.map((row) => displayUserName(row) + ';' + row.email.toLowerCase())}
          onInputChange={(event, newInputValue) => {
            setSearchInput(newInputValue?.split(';')[0] ?? '');
          }}
          onChange={handleSearchSubmit}
          defaultValue={searchQuery}
          key={searchQuery}
          renderOption={renderUserOption}
        />
      </TableTopBar>
      <Table sx={{minWidth: 650}} size="small" aria-label="users">
        <TableHead>
          <TableRow>
            <TableCell></TableCell>
            <TableCell>Name</TableCell>
            <TableCell>Email</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.id}>
              <TableCell>
                <Link
                  to={`/users/${row.email.toLowerCase()}`}
                  sx={{textDecoration: 'none', color: 'inherit'}}
                  component={RouterLink}>
                  <UserAvatar name={displayUserName(row)} size={24} variant={'body1'} />
                </Link>
              </TableCell>
              <TableCell>
                <Link
                  to={`/users/${row.email.toLowerCase()}`}
                  sx={{textDecoration: 'none', color: 'inherit'}}
                  component={RouterLink}>
                  {displayUserName(row)}
                </Link>
              </TableCell>
              <TableCell>
                <Link
                  to={`/users/${row.email.toLowerCase()}`}
                  sx={{textDecoration: 'none', color: 'inherit'}}
                  component={RouterLink}>
                  {row.email.toLowerCase()}
                </Link>
              </TableCell>
            </TableRow>
          ))}
          {emptyRows > 0 && (
            <TableRow style={{height: 37 * emptyRows}}>
              <TableCell colSpan={6} />
            </TableRow>
          )}
        </TableBody>
        <TableFooter>
          <TableRow>
            <TablePagination
              rowsPerPageOptions={perPage}
              colSpan={3}
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
  );
}
