import {Autocomplete, Grid, Paper, Stack, TextField} from '@mui/material';
import CreateUpdateGroup from '../../groups/CreateUpdate';
import {OktaUser, App, AppGroup} from '../../../api/apiSchemas';
import {renderUserOption} from '../../../components/TableTopBar';
import {displayUserName, sortGroupMemberRecords, sortGroupMembers} from '../../../helpers';
import React from 'react';

interface AppsAdminActionGroupProps {
  currentUser: OktaUser;
  app: App;
  onSearchSubmit?: (appGroup: AppGroup[]) => void;
}

export const AppsAdminActionGroup: React.FC<AppsAdminActionGroupProps> = ({currentUser, app, onSearchSubmit}) => {
  const allMembers: Record<string, OktaUser> = {};
  const memberGroups: Record<string, AppGroup[]> = {};
  [app.active_non_owner_app_groups].forEach((appGroupList) => {
    (appGroupList ?? []).forEach((appGroup) => {
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
  });

  const handleSearchSubmit = (_: unknown, newValue: string | null) => {
    const email = newValue?.split(';')[1] ?? '';
    const appGroups = memberGroups[email] ?? app.active_non_owner_app_groups;
    if (!!onSearchSubmit) {
      onSearchSubmit(appGroups);
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
        <Stack direction="row" width="100%" justifyContent="space-between">
          <CreateUpdateGroup defaultGroupType={'app_group'} currentUser={currentUser} app={app} />
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
          />
        </Stack>
      </Paper>
    </Grid>
  );
};
