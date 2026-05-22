import {Autocomplete, Button, Grid, Paper, TextField, Box} from '@mui/material';
import UnfoldLessIcon from '@mui/icons-material/UnfoldLess';
import UnfoldMoreIcon from '@mui/icons-material/UnfoldMore';
import CreateUpdateGroup from '../../groups/CreateUpdate';
import {OktaUser, App, AppGroup} from '../../../api/apiSchemas';
import {renderUserOption} from '../../../components/TableTopBar';
import {displayUserName, sortGroupMemberRecords} from '../../../helpers';
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

    const allMembersRef = React.useRef(allMembers);
    const memberGroupsRef = React.useRef(memberGroups);
    const appGroupsRef = React.useRef(app.active_non_owner_app_groups);
    const onSearchSubmitRef = React.useRef(onSearchSubmit);

    allMembersRef.current = allMembers;
    memberGroupsRef.current = memberGroups;
    appGroupsRef.current = app.active_non_owner_app_groups;
    onSearchSubmitRef.current = onSearchSubmit;

    const handleSearchSubmit = React.useCallback((_: unknown, newValue: string | null) => {
      const q = newValue?.trim().toLowerCase() ?? '';
      const fallback = appGroupsRef.current ?? [];
      if (!q) {
        onSearchSubmitRef.current?.(fallback);
        return;
      }
      const matchedById: Record<string, AppGroup> = {};
      Object.values(allMembersRef.current).forEach((user) => {
        const name = displayUserName(user).toLowerCase();
        const email = user.email.toLowerCase();
        if (!name.includes(q) && !email.includes(q)) return;
        (memberGroupsRef.current[email] ?? []).forEach((g) => {
          if (g.id) matchedById[g.id] = g;
        });
      });
      onSearchSubmitRef.current?.(Object.values(matchedById));
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
                options={sortedUserOptions}
                onChange={handleSearchSubmit}
                renderOption={renderUserOption}
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
