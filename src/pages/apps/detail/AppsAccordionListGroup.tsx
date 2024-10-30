import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Divider,
  Grid,
  Link,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableFooter,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import {App, AppGroup, OktaUserGroupMember} from '../../../api/apiSchemas';
import React from 'react';
import {displayUserName} from '../../../helpers';
import {EmptyListEntry} from '../../../components/EmptyListEntry';
import Ending from '../../../components/Ending';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import {Link as RouterLink, useParams} from 'react-router-dom';

interface AppAccordionListGroupProps {
  app_group?: AppGroup[];
}

export const AppsAccordionListGroup: React.FC<AppAccordionListGroupProps> = ({app_group}) => {
  const [expanded, setExpanded] = React.useState<string | false>(false);

  const handleChange = (panel: string) => (event: React.SyntheticEvent, newExpanded: boolean) => {
    setExpanded(newExpanded ? panel : false);
  };
  return (
    <React.Fragment>
      {app_group &&
        app_group?.map((appGroup) => (
          <Grid xs={12} item key={appGroup.id}>
            <TableContainer component={Paper}>
              <Table sx={{minWidth: 325}} aria-label="app group owners" size="medium">
                <TableHead>
                  <TableRow>
                    <TableCell colSpan={2}>
                      <Stack direction="column" spacing={1}>
                        <Typography variant="h6" color="primary">
                          <Link
                            to={`/groups/${appGroup.name}`}
                            sx={{
                              textDecoration: 'none',
                              color: 'inherit',
                            }}
                            component={RouterLink}>
                            {appGroup.name}
                          </Link>
                        </Typography>
                        <Typography variant="body1" color="grey">
                          Can manage app and implicitly own all app groups
                        </Typography>
                      </Stack>
                    </TableCell>
                    <TableCell>
                      <Box
                        sx={{
                          display: 'flex',
                          justifyContent: 'flex-end',
                          alignItems: 'right',
                        }}>
                        <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                        Total Owners: {appGroup.active_user_ownerships?.length || 0}
                      </Box>
                    </TableCell>
                  </TableRow>
                </TableHead>
                <TableBody className="accordion-body">
                  <TableRow>
                    <Accordion expanded={expanded === 'owners'} onChange={handleChange('owners')}>
                      <AccordionSummary expandIcon={<ExpandMoreIcon />}>OwnerList</AccordionSummary>
                      <AccordionDetails>Owners list goes here</AccordionDetails>
                    </Accordion>
                  </TableRow>
                  <TableRow>
                    <Accordion expanded={expanded === 'members'} onChange={handleChange('members')}>
                      <AccordionSummary expandIcon={<ExpandMoreIcon />}>MemberList</AccordionSummary>
                      <AccordionDetails>Members list goes here</AccordionDetails>
                    </Accordion>
                  </TableRow>
                </TableBody>
              </Table>
            </TableContainer>
          </Grid>
        ))}
    </React.Fragment>
  );
  /*
  return (
    <React.Fragment>
      <Grid item xs= key={group_id + 'owners'}>
        <TableContainer component={Paper}>
          <Table sx={{minWidth: 325}} size="small" aria-label="app group owners">
            <TableHead>
              <TableRow>
                <TableCell colSpan={2}>
                  <Stack direction="column">
                    <Typography variant="h6" color="primary">
                      <Link
                        to={`/groups/${appGroup.name}`}
                        sx={{
                          textDecoration: 'none',
                          color: 'inherit',
                        }}
                        component={RouterLink}>
                        {appGroup.name} Group Owners
                      </Link>
                    </Typography>
                    <Typography variant="body1" color="grey">
                      Can manage membership of Group
                    </Typography>
                  </Stack>
                </TableCell>
                <TableCell>
                  <Box
                    sx={{
                      display: 'flex',
                      justifyContent: 'flex-end',
                      alignItems: 'right',
                    }}>
                    <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                    Total Owners: {Object.keys(groupMemberships(appGroup.active_user_ownerships)).length}
                  </Box>
                </TableCell>
              </TableRow>
                <TableRow>
                  <TableCell>Name</TableCell>
                  <TableCell>Email</TableCell>
                  <TableCell>Ending</TableCell>
                </TableRow>
                </TableHead>
                <TableBody>
                  {Object.keys(groupMemberships(appGroup.active_user_ownerships)).length > 0 ? (
                        Object.entries(groupMemberships(appGroup.active_user_ownerships))
                          .sort(sortGroupMembers)
                          .map(([userId, users]: [string, Array<OktaUserGroupMember>]) => (
                            <TableRow key={userId}>
                              <TableCell>
                                <Link
                                  to={`/users/${users[0].active_user?.email.toLowerCase()}`}
                                  sx={{
                                    textDecoration: 'none',
                                    color: 'inherit',
                                  }}
                                  component={RouterLink}>
                                  {displayUserName(users[0].active_user)}
                                </Link>
                              </TableCell>
                              <TableCell>
                                <Link
                                  to={`/users/${users[0].active_user?.email.toLowerCase()}`}
                                  sx={{
                                    textDecoration: 'none',
                                    color: 'inherit',
                                  }}
                                  component={RouterLink}>
                                  {users[0].active_user?.email.toLowerCase()}
                                </Link>
                              </TableCell>
                              <TableCell>
                                <Ending memberships={users} />
                              </TableCell>
                            </TableRow>
                
                </TableBody>
          </React.Fragment>
  )
  */
};
