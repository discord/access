import React from 'react';
import Paper from '@mui/material/Paper';
import Typography from '@mui/material/Typography';
import Box from '@mui/material/Box';
import {OktaUserGroupMember, RoleGroupMap} from '../api/apiSchemas';
import AccessHistoryTable from './AccessHistoryTable';
import InfoOutlined from '@mui/icons-material/InfoOutlined';
import Accordion from '@mui/material/Accordion';
import AccordionSummary from '@mui/material/AccordionSummary';
import AccordionDetails from '@mui/material/AccordionDetails';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';

type AccessAuditEntry = OktaUserGroupMember | RoleGroupMap;

type SubjectType = 'user' | 'role';

interface AccessHistoryProps {
  subjectType: SubjectType;
  subjectName: string;
  groupName: string;
  auditHistory: AccessAuditEntry[];
  alternativeRoleMappings?: RoleGroupMap[];
}

export default function AccessHistory({
  subjectType,
  subjectName,
  groupName,
  auditHistory,
  alternativeRoleMappings,
}: AccessHistoryProps) {
  const currentAccess = auditHistory.filter(
    (audit) => audit.ended_at == null || (audit.ended_at && new Date(audit.ended_at) > new Date()),
  );
  const pastAccess = auditHistory.filter((audit) => audit.ended_at != null && new Date(audit.ended_at) <= new Date());
  // Info card for empty state
  if (currentAccess.length === 0 && pastAccess.length === 0) {
    return (
      <Box>
        <Paper sx={{p: 2, mt: 1, display: 'flex', alignItems: 'center', gap: 2, backgroundColor: 'background.default'}}>
          <Box sx={{display: 'flex', alignItems: 'center'}}>
            <InfoOutlined color="info" sx={{fontSize: 36, mr: 2}} />
          </Box>
          <Box>
            <Typography variant="subtitle1" sx={{fontWeight: 600}}>
              No prior access history
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {subjectType === 'user'
                ? `This user has never had access to this group. Approving this request will grant access for the first time.`
                : `This role has never had access to this group. Approving this request will grant access for the first time.`}
            </Typography>
          </Box>
        </Paper>
      </Box>
    );
  }

  return (
    <Box>
      <Accordion defaultExpanded sx={{mt: 1}}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography variant="h6" sx={{fontWeight: 600}}>
            {`${subjectName} Access History to ${groupName}`}
          </Typography>
        </AccordionSummary>
        <AccordionDetails>
          {/* Current Access Section */}
          <Typography variant="subtitle1" color="success.main" sx={{mb: 1, fontWeight: 500}}>
            {`Current Access${currentAccess.length > 0 ? ` (${currentAccess.length} active membership${currentAccess.length !== 1 ? 's' : ''})` : ''}`}
          </Typography>
          <AccessHistoryTable
            accessEntries={currentAccess as any}
            highlightColor="success"
            showEndedBy={false}
            emptyMessage="No current access"
          />
          <hr />
          {/* Past Access Section */}
          <Box sx={{mt: 2}}>
            <Typography variant="subtitle1" color="text.secondary" sx={{mb: 1, fontWeight: 500}}>
              {`Past Access${pastAccess.length > 0 ? ` (${pastAccess.length} previous membership${pastAccess.length !== 1 ? 's' : ''})` : ''}`}
            </Typography>
            <AccessHistoryTable
              accessEntries={pastAccess as any}
              highlightColor="danger"
              showEndedBy={true}
              emptyMessage="No previous access"
            />
          </Box>
        </AccordionDetails>
      </Accordion>
    </Box>
  );
}
