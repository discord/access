import React from 'react';
import Paper from '@mui/material/Paper';
import Typography from '@mui/material/Typography';
import Box from '@mui/material/Box';
import Link from '@mui/material/Link';
import {Link as RouterLink} from 'react-router-dom';
import {OktaUserGroupMember} from '../api/apiSchemas';
import AccessHistoryTable from './AccessHistoryTable';
import AccessHistorySummary from './AccessHistorySummary';
import RoleSuggestionTable from './RoleSuggestionTable';

interface UserAccessHistoryProps {
  userGroupHistory: OktaUserGroupMember[];
  groupName: string;
  requesterEmail: string;
  alternativeRoleMappings?: any[]; // RoleGroupMap[] from API
}

export default function UserAccessHistory({
  userGroupHistory,
  groupName,
  requesterEmail,
  alternativeRoleMappings,
}: UserAccessHistoryProps) {
  // Current access is any audit with no end date or an end date in the future.
  const currentAccess = userGroupHistory.filter(
    (audit) => audit.ended_at == null || (audit.ended_at && new Date(audit.ended_at) > new Date()),
  );
  // Past access is any audit with an end date in the past.
  const pastAccess = userGroupHistory.filter(
    (audit) => audit.ended_at != null && new Date(audit.ended_at) <= new Date(),
  );
  // Check if user has current role-based access to this group
  const hasCurrentRoleBasedAccess = currentAccess.some((audit) => audit.role_group_mapping != null);

  return (
    <Box>
      {/* Only show Role Suggestions if user does NOT have current role-based access */}
      {!hasCurrentRoleBasedAccess && <RoleSuggestionTable roleMappings={alternativeRoleMappings ?? []} />}
      {/* Combined Access History Card */}
      <Paper sx={{p: 2, mt: 1}}>
        <Typography variant="h6" sx={{mb: 2, fontWeight: 600}}>
          User's Access History to {groupName}
        </Typography>
        {/* Current Access Section */}
        <Typography variant="subtitle1" color="success.main" sx={{mb: 1, fontWeight: 500}}>
          {`Current Access${currentAccess.length > 0 ? ` (${currentAccess.length} active membership${currentAccess.length !== 1 ? 's' : ''})` : ''}`}
        </Typography>
        <AccessHistoryTable
          accessEntries={currentAccess}
          highlightColor="success"
          showEndedBy={false}
          emptyMessage="No current access"
        />
        {/* Past Access Section */}
        <Box sx={{mt: 2}}>
          <Typography variant="subtitle1" color="text.secondary" sx={{mb: 1, fontWeight: 500}}>
            {`Past Access${pastAccess.length > 0 ? ` (${pastAccess.length} previous membership${pastAccess.length !== 1 ? 's' : ''})` : ''}`}
          </Typography>
          <AccessHistoryTable
            accessEntries={pastAccess}
            highlightColor="danger"
            showEndedBy={true}
            emptyMessage="No previous access"
          />
        </Box>
      </Paper>
    </Box>
  );
}
