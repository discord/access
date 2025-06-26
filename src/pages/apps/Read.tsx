import React from 'react';
import {useParams} from 'react-router-dom';

import Container from '@mui/material/Container';
import Grid from '@mui/material/Grid';

import {useGetAppById} from '../../api/apiComponents';
import {App, AppGroup} from '../../api/apiSchemas';

import {useCurrentUser} from '../../authentication';
import NotFound from '../NotFound';
import Loading from '../../components/Loading';
import {AppsAccordionListGroup, AppsAdminActionGroup, AppsHeader} from './components/';
import ChangeTitle from '../../tab-title';

export default function ReadApp() {
  const currentUser = useCurrentUser();

  const {id} = useParams();
  const {data, isError, isLoading} = useGetAppById({
    pathParams: {appId: id ?? ''},
  });
  const [nonOwnerAppGroups, setNonOwnerAppGroups] = React.useState<AppGroup[]>(data?.active_non_owner_app_groups || []);
  const [isExpanded, setIsExpanded] = React.useState(true);

  const app = data ?? ({} as App);

  React.useEffect(() => {
    setNonOwnerAppGroups(app?.active_non_owner_app_groups || []);
  }, [data]);

  const handleToggleExpand = (expanded: boolean) => {
    setIsExpanded(expanded);
  };

  if (isError) {
    return <NotFound />;
  }

  if (isLoading) {
    return <Loading />;
  }

  return (
    <React.Fragment>
      <ChangeTitle title={app.name} />
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
          <AppsAdminActionGroup
            app={app}
            currentUser={currentUser}
            onSearchSubmit={setNonOwnerAppGroups}
            onToggleExpand={handleToggleExpand}
            isExpanded={isExpanded}
          />
          {app.active_non_owner_app_groups && (
            <AppsAccordionListGroup
              app_group={app.active_non_owner_app_groups}
              list_group_title={nonOwnerAppGroups.length > 1 ? 'App Groups' : 'App Group'}
              isExpanded={isExpanded}
            />
          )}
        </Grid>
      </Container>
    </React.Fragment>
  );
}
