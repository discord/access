import React, {useEffect, useState} from 'react';
import {Link as RouterLink, useNavigate, useParams} from 'react-router-dom';

import Container from '@mui/material/Container';
import Grid from '@mui/material/Grid';
import {groupBy} from '../../helpers';
import {useGetAppById} from '../../api/apiComponents';
import {App, AppGroup, OktaUserGroupMember} from '../../api/apiSchemas';

import {useCurrentUser} from '../../authentication';
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
      <Container maxWidth="lg" sx={{my: 4}}>
        <Grid container spacing={3}>
          <AppsHeader app={app} currentUser={currentUser} />
          {app.active_owner_app_groups && (
            <AppsAccordionListGroup
              app_group={app.active_owner_app_groups}
              list_group_title={'Owner Group'}
              list_group_description={'Owners can manage all app groups as implicit owners'}
            />
          )}
          <AppsAdminActionGroup app={app} currentUser={currentUser} onSearchSubmit={setNonOwnerAppGroups} />
          {nonOwnerAppGroups && (
            <AppsAccordionListGroup
              app_group={nonOwnerAppGroups}
              list_group_title={nonOwnerAppGroups.length > 1 ? 'App Groups' : 'App Group'}
            />
          )}
        </Grid>
      </Container>
    </React.Fragment>
  );
}
