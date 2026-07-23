import {Button, Grid, Paper, Box} from '@mui/material';
import UnfoldLessIcon from '@mui/icons-material/UnfoldLess';
import UnfoldMoreIcon from '@mui/icons-material/UnfoldMore';
import CreateUpdateGroup from '../../groups/CreateUpdate';
import DebouncedSearchField from '../../../components/DebouncedSearchField';
import {OktaUserDetail, AppDetail} from '../../../api/apiSchemas';
import React from 'react';

interface AppsAdminActionGroupProps {
  currentUser: OktaUserDetail;
  app: AppDetail;
  // Emits the raw search query. Filtering by group name or member is computed
  // server-side (GET /api/apps/{id}/groups?q=…) so the page no longer needs
  // every member loaded client-side.
  onSearchChange?: (q: string) => void;
  onToggleExpand?: (expanded: boolean) => void;
  isExpanded?: boolean;
}

export const AppsAdminActionGroup: React.FC<AppsAdminActionGroupProps> = React.memo(
  ({currentUser, app, onSearchChange, onToggleExpand, isExpanded = false}) => {
    const onSearchChangeRef = React.useRef(onSearchChange);
    onSearchChangeRef.current = onSearchChange;

    const handleSearchChange = React.useCallback((q: string) => {
      onSearchChangeRef.current?.(q);
    }, []);

    const onToggleExpandRef = React.useRef(onToggleExpand);
    const isExpandedRef = React.useRef(isExpanded);

    onToggleExpandRef.current = onToggleExpand;
    isExpandedRef.current = isExpanded;

    const handleToggleExpand = React.useCallback(() => {
      if (!!onToggleExpandRef.current) {
        onToggleExpandRef.current(!isExpandedRef.current);
      }
    }, []);

    return (
      <Grid item xs={12} className={'app-detail app-detail-admin-action-group'}>
        <Paper sx={{p: 2}}>
          <Box
            sx={{
              display: 'flex',
              flexWrap: 'wrap',
              alignItems: 'center',
              gap: 2,
            }}>
            <Box sx={{flexShrink: 0}}>
              <CreateUpdateGroup defaultGroupType={'app_group'} currentUser={currentUser} app={app} />
            </Box>
            <Box
              sx={{
                display: 'flex',
                flexWrap: 'wrap',
                alignItems: 'center',
                gap: 2,
                flex: '1 1 auto',
                minWidth: 0,
                marginLeft: 'auto',
                justifyContent: 'flex-end',
              }}>
              <DebouncedSearchField
                label="Search groups or users"
                onSearchChange={handleSearchChange}
                sx={{flex: '1 1 220px', minWidth: 0, maxWidth: 320}}
                autoFocus
              />
              <Button
                variant="contained"
                color="primary"
                size="small"
                startIcon={isExpanded ? <UnfoldLessIcon /> : <UnfoldMoreIcon />}
                onClick={handleToggleExpand}
                sx={{flexShrink: 0}}>
                {isExpanded ? 'Collapse' : 'Expand'}
              </Button>
            </Box>
          </Box>
        </Paper>
      </Grid>
    );
  },
);
