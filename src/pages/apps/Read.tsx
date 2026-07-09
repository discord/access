import React from 'react';
import {useParams} from 'react-router-dom';

import Box from '@mui/material/Box';
import CircularProgress from '@mui/material/CircularProgress';
import Container from '@mui/material/Container';
import Grid from '@mui/material/Grid';

import {useAppById, useAppGroupsById} from '../../api/apiComponents';
import {useInfiniteAppGroups} from '../../api/infiniteAppGroups';
import {AppDetail} from '../../api/apiSchemas';

import {useCurrentUser} from '../../authentication';
import NotFound from '../NotFound';
import Loading from '../../components/Loading';
import {InfiniteScrollSentinel} from '../../components/InfiniteScrollSentinel';
import {AppsAccordionListGroup, AppsAdminActionGroup, AppsHeader} from './components/';
import ChangeTitle from '../../tab-title';
import AppGroupLifecyclePluginData from '../../components/AppGroupLifecyclePluginData';

export default function ReadApp() {
  const currentUser = useCurrentUser();

  const {id} = useParams();
  const appId = id ?? '';

  const {data, isError, isLoading} = useAppById({
    pathParams: {appId},
  });

  // App groups are no longer inlined on the app payload. Owners (few) are
  // fetched whole; non-owner groups load page-by-page on scroll. Filtering by
  // group name or member is computed server-side via the `q` query param.
  const [searchQuery, setSearchQuery] = React.useState('');
  const [isExpanded, setIsExpanded] = React.useState(false);

  // React Router reuses this component instance when only :id changes; reset
  // the search so it doesn't leak across apps (the infinite query is keyed by
  // app id + query, so it refetches on its own).
  React.useEffect(() => {
    setSearchQuery('');
  }, [id]);

  const {data: ownerGroupsData} = useAppGroupsById({
    pathParams: {appId},
    queryParams: {owner: true},
  });

  const nonOwnerGroupsQuery = useInfiniteAppGroups(appId, false, searchQuery);

  const handleToggleExpand = React.useCallback((expanded: boolean) => {
    setIsExpanded((prev) => (prev === expanded ? prev : expanded));
  }, []);

  const handleSearchChange = React.useCallback((q: string) => {
    setSearchQuery((prev) => (prev === q ? prev : q));
  }, []);

  if (isError) {
    return <NotFound />;
  }

  if (isLoading) {
    return <Loading />;
  }

  const app = data ?? ({} as AppDetail);
  const ownerGroups = ownerGroupsData?.items ?? [];
  const nonOwnerGroups = nonOwnerGroupsQuery.data?.pages.flatMap((p) => p.items) ?? [];
  const isSearchActive = searchQuery.length > 0;

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
          {ownerGroups.length > 0 && (
            <AppsAccordionListGroup
              app_group={ownerGroups}
              list_group_title={'Owner Group'}
              list_group_description={'Owners can manage all app groups as implicit owners'}
            />
          )}
          <AppsAdminActionGroup
            key={id}
            app={app}
            currentUser={currentUser}
            onSearchChange={handleSearchChange}
            onToggleExpand={handleToggleExpand}
            isExpanded={isExpanded}
          />
          <AppsAccordionListGroup
            app_group={nonOwnerGroups}
            list_group_title={nonOwnerGroups.length > 1 ? 'App Groups' : 'App Group'}
            isExpanded={isExpanded || isSearchActive}
          />
          {nonOwnerGroupsQuery.isFetchingNextPage && (
            <Grid item xs={12}>
              <Box sx={{display: 'flex', justifyContent: 'center'}}>
                <CircularProgress size={24} />
              </Box>
            </Grid>
          )}
          {/* Load the next page of app groups when this scrolls into view. */}
          <Grid item xs={12} sx={{py: 0}}>
            <InfiniteScrollSentinel
              onVisible={() => nonOwnerGroupsQuery.fetchNextPage()}
              disabled={!nonOwnerGroupsQuery.hasNextPage || nonOwnerGroupsQuery.isFetchingNextPage}
            />
          </Grid>
        </Grid>
      </Container>
    </React.Fragment>
  );
}
