import React from 'react';
import Typography from '@mui/material/Typography';

import {OktaUserGroupMember} from '../api/apiSchemas';

interface AccessHistorySummaryProps {
  currentAccess: OktaUserGroupMember[];
  pastAccess: OktaUserGroupMember[];
}

export default function AccessHistorySummary({currentAccess, pastAccess}: AccessHistorySummaryProps) {
  const hasCurrentAccess = currentAccess.length > 0;
  const hasPastAccess = pastAccess.length > 0;

  let summaryText = '';

  if (hasCurrentAccess) {
    summaryText = `User currently has ${currentAccess.length} active membership${currentAccess.length !== 1 ? 's' : ''} to this group.`;
  } else if (hasPastAccess) {
    summaryText = `User has no current access to this group, but has had access in the past (${pastAccess.length} previous membership${pastAccess.length !== 1 ? 's' : ''}).`;
  } else {
    summaryText = 'User has never had access to this group before.';
  }

  return (
    <Typography variant="body2" color="text.secondary" sx={{mb: 3}}>
      {summaryText}
    </Typography>
  );
}
