import React from 'react';
import {Link as RouterLink, useSearchParams} from 'react-router-dom';

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

import {useCurrentUser} from '../../authentication';
import ChangeTitle from '../../tab-title';
import {emptyTableRows, perPage} from '../../helpers';
import {useServicePrincipals} from '../../api/apiComponents';
import {ServicePrincipalDetail} from '../../api/apiSchemas';
import TablePaginationActions from '../../components/actions/TablePaginationActions';
import TableTopBar, {TableTopBarAutocomplete} from '../../components/TableTopBar';
import CreateUpdateServicePrincipal from './CreateUpdate';
import ServicePrincipalOwners from './Owners';

export default function ListServicePrincipals() {
  const currentUser = useCurrentUser();
  const [searchParams, setSearchParams] = useSearchParams();

  const searchQuery = searchParams.get('q');
  const page = parseInt(searchParams.get('page') ?? '0', 10);
  const rowsPerPage = parseInt(searchParams.get('size') ?? '20', 10);
  const [searchInput, setSearchInput] = React.useState(searchQuery ?? '');

  const {data} = useServicePrincipals({
    queryParams: Object.assign({page: page + 1, size: rowsPerPage}, searchQuery == null ? null : {q: searchQuery}),
  });
  const {data: searchData} = useServicePrincipals({queryParams: {page: 1, size: 10, q: searchInput}});

  const rows = data?.items ?? [];
  const totalRows = data?.total ?? 0;
  const emptyRows = emptyTableRows(page, rowsPerPage, rows.length);

  const setParams = (update: (params: URLSearchParams) => void) =>
    setSearchParams((params) => {
      update(params);
      return params;
    });

  return (
    <>
      <ChangeTitle title="Service Principals" />
      <TableContainer component={Paper}>
        <TableTopBar title="Service Principals">
          <CreateUpdateServicePrincipal currentUser={currentUser} />
          <TableTopBarAutocomplete
            options={(searchData?.items ?? []).map((row: ServicePrincipalDetail) => row.display_name)}
            onChange={(event, newValue) =>
              setParams((params) => {
                params.set('page', '0');
                if (newValue == null) {
                  params.delete('q');
                } else {
                  params.set('q', newValue);
                }
              })
            }
            onInputChange={(event, newInputValue) => setSearchInput(newInputValue)}
            defaultValue={searchQuery}
          />
        </TableTopBar>
        <Table sx={{minWidth: 650}} size="small" aria-label="service principals">
          <TableHead>
            <TableRow>
              <TableCell>Name</TableCell>
              <TableCell>Provider</TableCell>
              <TableCell>Owners</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((row: ServicePrincipalDetail) => (
              <TableRow key={row.id}>
                <TableCell>
                  <Link
                    to={`/service-principals/${row.id}`}
                    sx={{textDecoration: 'none', color: 'inherit'}}
                    component={RouterLink}>
                    {row.display_name}
                  </Link>
                </TableCell>
                <TableCell>{row.provider}</TableCell>
                <TableCell>
                  <ServicePrincipalOwners owners={row.owners} />
                </TableCell>
              </TableRow>
            ))}
            {emptyRows > 0 && (
              <TableRow style={{height: 33 * emptyRows}}>
                <TableCell colSpan={3} />
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
                onPageChange={(event, newPage) => setParams((params) => params.set('page', newPage.toString(10)))}
                onRowsPerPageChange={(event) =>
                  setParams((params) => {
                    params.set('page', '0');
                    params.set('size', event.target.value);
                  })
                }
                ActionsComponent={TablePaginationActions}
                slotProps={{select: {inputProps: {'aria-label': 'rows per page'}, native: true}}}
              />
            </TableRow>
          </TableFooter>
        </Table>
      </TableContainer>
    </>
  );
}
