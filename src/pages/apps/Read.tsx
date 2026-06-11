import React from 'react';
import {useParams} from 'react-router-dom';

import Container from '@mui/material/Container';
import Grid from '@mui/material/Grid';
import Paper from '@mui/material/Paper';

import {useAppById} from '../../api/apiComponents';
import {AppDetail, AppGroupDetail} from '../../api/apiSchemas';

import {useCurrentUser} from '../../authentication';
import NotFound from '../NotFound';
import Loading from '../../components/Loading';
import {AppsAccordionListGroup, AppsAdminActionGroup, AppsHeader} from './components/';
import ChangeTitle from '../../tab-title';
import AppGroupLifecyclePluginData from '../../components/AppGroupLifecyclePluginData';

export default function ReadApp() {
  const currentUser = useCurrentUser();

  const {id} = useParams();
  const {data, isError, isLoading} = useAppById({
    pathParams: {appId: id ?? ''},
  });
  const [nonOwnerAppGroups, setNonOwnerAppGroups] = React.useState<AppGroupDetail[]>([]);
  const [isExpanded, setIsExpanded] = React.useState(false);
  const [isSearchActive, setIsSearchActive] = React.useState(false);

  const app = data ?? ({} as AppDetail);

  const initialNonOwnerAppGroups = React.useMemo(() => {
    return app?.active_non_owner_app_groups || [];
  }, [app?.active_non_owner_app_groups]);

  React.useEffect(() => {
    setNonOwnerAppGroups(initialNonOwnerAppGroups);
  }, [initialNonOwnerAppGroups]);

  const handleToggleExpand = React.useCallback((expanded: boolean) => {
    setIsExpanded((prev) => {
      // Only update if the value actually changed
      if (prev === expanded) {
        return prev;
      }
      return expanded;
    });
  }, []);

  const handleSearchSubmit = React.useCallback((newAppGroups: AppGroupDetail[], isActive: boolean) => {
    setNonOwnerAppGroups((prev) => {
      // Only update if the groups actually changed
      if (prev.length === newAppGroups.length && prev.every((group, index) => group.id === newAppGroups[index]?.id)) {
        return prev;
      }
      return newAppGroups;
    });
    setIsSearchActive((prev) => (prev === isActive ? prev : isActive));
  }, []);

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
          {(app as any)?.app_group_lifecycle_plugin && (
            <Grid item xs={12}>
              <AppGroupLifecyclePluginData
                entityType="app"
                pluginId={(app as any).app_group_lifecycle_plugin}
                currentConfig={
                  (app as any)?.plugin_data
                    ? (app as any).plugin_data[(app as any).app_group_lifecycle_plugin]?.configuration || {}
                    : {}
                }
                currentStatus={
                  (app as any)?.plugin_data
                    ? (app as any).plugin_data[(app as any).app_group_lifecycle_plugin]?.status || {}
                    : {}
                }
              />
            </Grid>
          )}
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
            onSearchSubmit={handleSearchSubmit}
            onToggleExpand={handleToggleExpand}
            isExpanded={isExpanded}
          />
          {app.active_non_owner_app_groups && (
            <AppsAccordionListGroup
              app_group={nonOwnerAppGroups}
              list_group_title={nonOwnerAppGroups.length > 1 ? 'App Groups' : 'App Group'}
              isExpanded={isExpanded || isSearchActive}
            />
          )}
        </Grid>
      </Container>
    </React.Fragment>
  );
}
