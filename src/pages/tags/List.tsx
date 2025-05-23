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
import Typography from '@mui/material/Typography';
import TextField from '@mui/material/TextField';
import Autocomplete from '@mui/material/Autocomplete';
import Box from '@mui/material/Box';

import {useCurrentUser} from '../../authentication';
import CreateUpdateTag from './CreateUpdate';
import ChangeTitle from '../../tab-title';
import {perPage} from '../../helpers';
import {useGetTags} from '../../api/apiComponents';
import TablePaginationActions from '../../components/actions/TablePaginationActions';
import TableTopBar, {TableTopBarAutocomplete} from '../../components/TableTopBar';

export default function ListTags() {
  const navigate = useNavigate();
  const currentUser = useCurrentUser();

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

  const {data, error, isLoading} = useGetTags({
    queryParams: Object.assign({page: page, per_page: rowsPerPage}, searchQuery == null ? null : {q: searchQuery}),
  });

  const {data: searchData} = useGetTags({
    queryParams: {page: 0, per_page: 10, q: searchInput},
  });

  const rows = data?.results ?? [];
  const totalRows = data?.total ?? 0;

  // If there's only one search result, just redirect to that app's page
  if (searchQuery != null && totalRows == 1) {
    navigate('/tags/' + rows[0].name, {
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
      setSearchParams((params) => {
        params.set('page', '0');
        params.set('q', newValue);
        return params;
      });
      setPage(0);
    }
    setSearchQuery(newValue);
  };

  return (
    <>
      <ChangeTitle title="Tags" />
      <TableContainer component={Paper}>
        <TableTopBar title="Tags">
          <CreateUpdateTag currentUser={currentUser}></CreateUpdateTag>
          <TableTopBarAutocomplete
            options={searchRows.map((row) => row.name)}
            onChange={handleSearchSubmit}
            onInputChange={(event, newInputValue) => setSearchInput(newInputValue)}
            defaultValue={searchQuery}
          />
        </TableTopBar>
        <Table sx={{minWidth: 650}} size="small" aria-label="apps">
          <TableHead>
            <TableRow>
              <TableCell>Name</TableCell>
              <TableCell>Description</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.id}>
                <TableCell>
                  <Link to={`/tags/${row.name}`} sx={{textDecoration: 'none', color: 'inherit'}} component={RouterLink}>
                    {row.name}
                  </Link>
                </TableCell>
                <TableCell>
                  <Link to={`/tags/${row.name}`} sx={{textDecoration: 'none', color: 'inherit'}} component={RouterLink}>
                    {(row.description ?? '').length > 115
                      ? row.description?.substring(0, 114) + '...'
                      : row.description ?? ''}
                  </Link>
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
    </>
  );
}
