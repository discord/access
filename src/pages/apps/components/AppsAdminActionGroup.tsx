import {Autocomplete, Button, Grid, Paper, Stack, TextField, Box} from '@mui/material';
import CreateUpdateGroup from '../../groups/CreateUpdate';
import {OktaUser, App, AppGroup} from '../../../api/apiSchemas';
import {renderUserOption} from '../../../components/TableTopBar';
import {displayUserName, extractEmailFromDisplayName, sortGroupMemberRecords, sortGroupMembers} from '../../../helpers';
import React from 'react';

interface AppsAdminActionGroupProps {
  currentUser: OktaUser;
  app: App;
  onSearchSubmit?: (appGroup: AppGroup[]) => void;
  onToggleExpand?: (expanded: boolean) => void;
  isExpanded?: boolean;
}

export const AppsAdminActionGroup: React.FC<AppsAdminActionGroupProps> = React.memo(
  ({currentUser, app, onSearchSubmit, onToggleExpand, isExpanded = false}) => {
    const {allMembers, memberGroups, sortedUserOptions} = React.useMemo(() => {
      const allMembers: Record<string, OktaUser> = {};
      const memberGroups: Record<string, AppGroup[]> = {};

      (app.active_non_owner_app_groups ?? []).forEach((appGroup) => {
        [appGroup.active_user_ownerships, appGroup.active_user_memberships].forEach((memberList) => {
          (memberList ?? []).forEach((member) => {
            const activeUser = member.active_user;
            if (activeUser) {
              allMembers[activeUser.id] = activeUser;
              const groups = (memberGroups[activeUser.email.toLowerCase()] ||= []);
              if (
                !groups.find((g) => {
                  return g.id === appGroup.id;
                })
              ) {
                groups.push(appGroup);
              }
            }
          });
        });
      });

      const sortedUserOptions = sortGroupMemberRecords(allMembers).map(
        (row) => `${displayUserName(row)} (${row.email.toLowerCase()})`,
      );

      return {allMembers, memberGroups, sortedUserOptions};
    }, [app.active_non_owner_app_groups]);

    const memberGroupsRef = React.useRef(memberGroups);
    const appGroupsRef = React.useRef(app.active_non_owner_app_groups);
    const onSearchSubmitRef = React.useRef(onSearchSubmit);

    memberGroupsRef.current = memberGroups;
    appGroupsRef.current = app.active_non_owner_app_groups;
    onSearchSubmitRef.current = onSearchSubmit;

    const handleSearchSubmit = React.useCallback((_: unknown, newValue: string | null) => {
      const email = extractEmailFromDisplayName(newValue);
      const appGroups = memberGroupsRef.current[email] ?? appGroupsRef.current;
      if (!!onSearchSubmitRef.current) {
        onSearchSubmitRef.current(appGroups);
      }
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
        <Paper
          sx={{
            p: 2,
            display: 'flex',
            alignItems: 'center',
          }}>
          <Box
            sx={{
              display: 'flex',
              width: '100%',
              flexDirection: {xs: 'column', md: 'row'},
              gap: {xs: 2, md: 0},
              alignItems: {xs: 'stretch', md: 'center'},
              justifyContent: 'space-between',
              '@media (max-width: 680px)': {
                flexDirection: 'column',
                gap: 2,
                alignItems: 'stretch',
              },
              '@media (min-width: 681px)': {
                flexDirection: 'row',
                gap: 0,
                alignItems: 'center',
              },
            }}>
            <Box
              sx={{
                flex: {xs: 'none', md: '1 1 auto'},
                '@media (max-width: 680px)': {
                  flex: 'none',
                },
                '@media (min-width: 681px)': {
                  flex: '1 1 auto',
                },
              }}>
              <CreateUpdateGroup defaultGroupType={'app_group'} currentUser={currentUser} app={app} />
            </Box>
            <Box
              sx={{
                display: 'flex',
                flexDirection: {xs: 'column', md: 'row'},
                gap: 2,
                alignItems: {xs: 'stretch', md: 'center'},
                flex: {xs: 'none', md: '0 0 auto'},
                minWidth: {md: 'auto', lg: '400px'},
                '@media (max-width: 680px)': {
                  flexDirection: 'column',
                  flex: 'none',
                  alignItems: 'stretch',
                },
                '@media (min-width: 681px)': {
                  flexDirection: 'row',
                  flex: '0 0 auto',
                  alignItems: 'center',
                },
              }}>
              <Autocomplete
                size="small"
                sx={{
                  flex: {xs: '1 1 auto', md: '1 1 320px'},
                  minWidth: {xs: '100%', md: '200px'},
                  maxWidth: {md: '320px'},
                  '@media (max-width: 680px)': {
                    flex: '1 1 auto',
                    minWidth: '100%',
                  },
                  '@media (min-width: 681px)': {
                    flex: '1 1 320px',
                    minWidth: '200px',
                    maxWidth: '320px',
                  },
                }}
                renderInput={(params) => <TextField {...params} label="Search" />}
                options={sortedUserOptions}
                onChange={handleSearchSubmit}
                renderOption={renderUserOption}
                autoHighlight
                autoSelect
                clearOnEscape
                freeSolo
                autoFocus
              />
              <Button
                variant="contained"
                color="primary"
                size="small"
                onClick={handleToggleExpand}
                sx={{
                  flex: {xs: 'none', md: '0 0 auto'},
                  minWidth: {xs: '100%', md: 'auto'},
                  '@media (max-width: 680px)': {
                    flex: 'none',
                    minWidth: '100%',
                  },
                  '@media (min-width: 681px)': {
                    flex: '0 0 auto',
                    minWidth: 'auto',
                  },
                }}>
                {isExpanded ? 'Collapse All' : 'Expand All'}
              </Button>
            </Box>
          </Box>
        </Paper>
      </Grid>
    );
  },
);
