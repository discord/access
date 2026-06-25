import React from 'react';
import {useParams} from 'react-router-dom';

import Box from '@mui/material/Box';
import Container from '@mui/material/Container';
import Grid from '@mui/material/Grid';
import Pagination from '@mui/material/Pagination';

import {useAppById, useAppGroupsById} from '../../api/apiComponents';
import {AppDetail} from '../../api/apiSchemas';

import {useCurrentUser} from '../../authentication';
import NotFound from '../NotFound';
import Loading from '../../components/Loading';
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

  // App groups are no longer inlined on the app payload; they're paginated
  // (owners and non-owners fetched separately) so one response can't
  // materialize every group's membership. Member-based filtering is computed
  // server-side via the `q` query param.
  const [page, setPage] = React.useState(1);
  const [searchQuery, setSearchQuery] = React.useState('');
  const [isExpanded, setIsExpanded] = React.useState(false);

  // React Router reuses this component instance when only :id changes, so the
  // pagination/search state would otherwise leak across apps (e.g. show app B
  // filtered by app A's query at page 2). Reset it whenever the app changes.
  React.useEffect(() => {
    setPage(1);
    setSearchQuery('');
  }, [id]);

  const {data: ownerGroupsData} = useAppGroupsById({
    pathParams: {appId},
    queryParams: {owner: true},
  });

  const {data: nonOwnerGroupsData} = useAppGroupsById({
    pathParams: {appId},
    // Omit `q` entirely when there's no search — the generated fetcher runs
    // queryParams through URLSearchParams, which serializes `undefined` to the
    // literal string "undefined" (`q=undefined`) rather than dropping it.
    queryParams: Object.assign({owner: false, page}, searchQuery ? {q: searchQuery} : null),
  });

  const handleToggleExpand = React.useCallback((expanded: boolean) => {
    setIsExpanded((prev) => (prev === expanded ? prev : expanded));
  }, []);

  const handleSearchChange = React.useCallback((q: string) => {
    setSearchQuery((prev) => (prev === q ? prev : q));
    setPage(1);
  }, []);

  if (isError) {
    return <NotFound />;
  }

  if (isLoading) {
    return <Loading />;
  }

  const app = data ?? ({} as AppDetail);
  const ownerGroups = ownerGroupsData?.items ?? [];
  const nonOwnerGroups = nonOwnerGroupsData?.items ?? [];
  const totalPages = nonOwnerGroupsData?.pages ?? 0;
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
          {totalPages > 1 && (
            <Grid item xs={12}>
              <Box sx={{display: 'flex', justifyContent: 'center'}}>
                <Pagination count={totalPages} page={page} onChange={(_, value) => setPage(value)} color="primary" />
              </Box>
            </Grid>
          )}
        </Grid>
      </Container>
    </React.Fragment>
  );
}
