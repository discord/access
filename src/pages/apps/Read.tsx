import React, {useEffect, useState} from 'react';
import {Link as RouterLink, useNavigate, useParams} from 'react-router-dom';

import Box from '@mui/material/Box';
import Chip from '@mui/material/Chip';
import Container from '@mui/material/Container';
import Divider from '@mui/material/Divider';
import Grid from '@mui/material/Grid';
import Link from '@mui/material/Link';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableFooter from '@mui/material/TableFooter';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';

import TagIcon from '@mui/icons-material/LocalOffer';

import Ending from '../../components/Ending';
import {groupBy, displayUserName} from '../../helpers';
import {isAccessAdmin, isAppOwnerGroupOwner} from '../../authorization';
import {useGetAppById} from '../../api/apiComponents';
import {App, AppGroup, OktaUserGroupMember} from '../../api/apiSchemas';

import {useCurrentUser} from '../../authentication';
import CreateUpdateGroup from '../groups/CreateUpdate';
import CreateUpdateApp from './CreateUpdate';
import DeleteApp from './Delete';
import NotFound from '../NotFound';
import Loading from '../../components/Loading';
import {AppsAccordionListGroup, AppsAdminActionGroup, AppsHeader} from './components/';

export default function ReadApp() {
  const currentUser = useCurrentUser();

  const {id} = useParams();
  const {data, isError, isLoading} = useGetAppById({
    pathParams: {appId: id ?? ''},
  });
  const [nonOwnerAppGroups, setNonOwnerAppGroups] = React.useState<AppGroup[]>(data?.active_non_owner_app_groups || []);
  const app = data ?? ({} as App);

  React.useEffect(() => {
    setNonOwnerAppGroups(app?.active_non_owner_app_groups || []);
  }, [data]);

  if (isError) {
    return <NotFound />;
  }

  if (isLoading) {
    return <Loading />;
  }

  return (
    <React.Fragment>
      <Container maxWidth="lg" sx={{mt: 4, mb: 4}}>
        <Grid container spacing={3}>
          <AppsHeader app={app} currentUser={currentUser} />
          {app.active_owner_app_groups && (
            <AppsAccordionListGroup app_group={app.active_owner_app_groups} list_group_title={'Owner Group'} />
          )}
          <AppsAdminActionGroup app={app} currentUser={currentUser} onSearchSubmit={setNonOwnerAppGroups} />
          {nonOwnerAppGroups && (
            <AppsAccordionListGroup app_group={nonOwnerAppGroups} list_group_title={'App Group(s)'} />
          )}
        </Grid>
      </Container>
    </React.Fragment>
  );
}
