import {Autocomplete, Button, Grid, Paper, Stack, TextField} from '@mui/material';
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

export const AppsAdminActionGroup: React.FC<AppsAdminActionGroupProps> = ({
  currentUser,
  app,
  onSearchSubmit,
  onToggleExpand,
  isExpanded = true,
}) => {
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

  const handleSearchSubmit = (_: unknown, newValue: string | null) => {
    const email = extractEmailFromDisplayName(newValue);
    const appGroups = memberGroups[email] ?? app.active_non_owner_app_groups;
    if (!!onSearchSubmit) {
      onSearchSubmit(appGroups);
    }
  };

  const handleToggleExpand = () => {
    if (!!onToggleExpand) {
      onToggleExpand(!isExpanded);
    }
  };

  return (
    <Grid item xs={12} className={'app-detail app-detail-admin-action-group'}>
      <Paper
        sx={{
          p: 2,
          display: 'flex',
          alignItems: 'center',
        }}>
        <Stack direction="row" width="100%" justifyContent="space-between" alignItems="center">
          <CreateUpdateGroup defaultGroupType={'app_group'} currentUser={currentUser} app={app} />
          <Stack direction="row" spacing={2} alignItems="center">
            <Autocomplete
              size="small"
              sx={{width: 320}}
              renderInput={(params) => <TextField {...params} label="Search" />}
              options={sortGroupMemberRecords(allMembers).map(
                (row) => `${displayUserName(row)} (${row.email.toLowerCase()})`,
              )}
              onChange={handleSearchSubmit}
              renderOption={renderUserOption}
              autoHighlight
              autoSelect
              clearOnEscape
              freeSolo
              autoFocus
            />
            <Button variant="outlined" size="small" onClick={handleToggleExpand}>
              {isExpanded ? 'Collapse All' : 'Expand All'}
            </Button>
          </Stack>
        </Stack>
      </Paper>
    </Grid>
  );
};
