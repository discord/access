import React from 'react';
import {useParams} from 'react-router-dom';

import Container from '@mui/material/Container';
import Divider from '@mui/material/Divider';
import Grid from '@mui/material/Grid';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableRow from '@mui/material/TableRow';
import Typography from '@mui/material/Typography';

import {useServicePrincipalById} from '../../api/apiComponents';
import {useCurrentUser} from '../../authentication';
import {isAccessAdmin} from '../../authorization';
import ActionTooltip from '../../components/ActionTooltip';
import Loading from '../../components/Loading';
import ChangeTitle from '../../tab-title';
import NotFound from '../NotFound';
import CreateUpdateServicePrincipal from './CreateUpdate';
import DeleteServicePrincipal from './Delete';
import ServicePrincipalOwners from '../../components/ServicePrincipalOwners';

export default function ReadServicePrincipal() {
  const currentUser = useCurrentUser();
  const {id} = useParams();

  const {data, isError, isLoading} = useServicePrincipalById({pathParams: {servicePrincipalId: id ?? ''}});

  if (isError) {
    return <NotFound />;
  }
  if (isLoading || data == null) {
    return <Loading />;
  }

  return (
    <React.Fragment>
      <ChangeTitle title={`${data.display_name} Service Principal`} />
      <Container maxWidth="lg" sx={{my: 4}}>
        <Grid container spacing={3}>
          <Grid size={12}>
            <Paper sx={{p: 2}}>
              <Stack direction="column" sx={{gap: 2}}>
                <Stack direction="column" sx={{alignItems: 'center', gap: 1, wordBreak: 'break-word'}}>
                  <Typography variant="h3" sx={{textAlign: 'center'}}>
                    {data.display_name}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    Service principal
                  </Typography>
                </Stack>
                {isAccessAdmin(currentUser) && (
                  <>
                    <Divider />
                    <Stack direction="row" sx={{justifyContent: 'center'}}>
                      <ActionTooltip title="Edit">
                        <CreateUpdateServicePrincipal currentUser={currentUser} servicePrincipal={data} />
                      </ActionTooltip>
                      <ActionTooltip title="Delete">
                        <DeleteServicePrincipal currentUser={currentUser} servicePrincipal={data} />
                      </ActionTooltip>
                    </Stack>
                  </>
                )}
              </Stack>
            </Paper>
          </Grid>
          <Grid size={12}>
            <Paper sx={{p: 2}}>
              <Table size="small" aria-label="service principal details">
                <TableBody>
                  <TableRow>
                    <TableCell component="th">ID</TableCell>
                    <TableCell>{data.id}</TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell component="th">Provider</TableCell>
                    <TableCell>{data.provider}</TableCell>
                  </TableRow>
                  {data.subject != null && (
                    <TableRow>
                      <TableCell component="th">Subject</TableCell>
                      <TableCell sx={{wordBreak: 'break-all'}}>{data.subject}</TableCell>
                    </TableRow>
                  )}
                  <TableRow>
                    <TableCell component="th">Owners</TableCell>
                    <TableCell>
                      <ServicePrincipalOwners owners={data.owners} />
                    </TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </Paper>
          </Grid>
        </Grid>
      </Container>
    </React.Fragment>
  );
}
