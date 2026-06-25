import {Autocomplete, Button, Grid, Paper, TextField, Box} from '@mui/material';
import UnfoldLessIcon from '@mui/icons-material/UnfoldLess';
import UnfoldMoreIcon from '@mui/icons-material/UnfoldMore';
import CreateUpdateGroup from '../../groups/CreateUpdate';
import {OktaUserDetail, AppDetail} from '../../../api/apiSchemas';
import React from 'react';

interface AppsAdminActionGroupProps {
  currentUser: OktaUserDetail;
  app: AppDetail;
  // Emits the raw user search query. Group filtering by member is computed
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

    // Debounce the emit so each keystroke doesn't fire its own server-side
    // GET /api/apps/{id}/groups?q=… request; the search runs once typing settles.
    const debounceRef = React.useRef<ReturnType<typeof setTimeout>>();
    const handleSearchChange = React.useCallback((_: unknown, newValue: string | null) => {
      const q = newValue?.trim() ?? '';
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }
      debounceRef.current = setTimeout(() => {
        onSearchChangeRef.current?.(q);
      }, 300);
    }, []);

    React.useEffect(() => {
      return () => {
        if (debounceRef.current) {
          clearTimeout(debounceRef.current);
        }
      };
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
              <Autocomplete
                size="small"
                sx={{flex: '1 1 220px', minWidth: 0, maxWidth: 320}}
                renderInput={(params) => <TextField {...params} label="Search Users" />}
                options={[]}
                onChange={handleSearchChange}
                onInputChange={handleSearchChange}
                clearOnEscape
                freeSolo
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
