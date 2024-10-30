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
interface GroupDetailListProps {
  member_list: OktaUserGroupMember[];
}

const GroupDetailList: React.FC<GroupDetailListProps> = ({member_list}) => {
  const sortGroupMembers = (
    [aUserId, aUsers]: [string, Array<OktaUserGroupMember>],
    [bUserId, bUsers]: [string, Array<OktaUserGroupMember>],
  ): number => {
    let aEmail = aUsers[0].active_user?.email ?? '';
    let bEmail = bUsers[0].active_user?.email ?? '';
    return aEmail.localeCompare(bEmail);
  };

  return (
    <TableContainer component={Paper}>
      <Table sx={{minWidth: 325}} size="small" aria-label="app owners">
        <TableHead>
          <TableRow>
            <TableCell>Name</TableCell>
            <TableCell>Email</TableCell>
            <TableCell>Ending</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {member_list.length > 0 ? (
            member_list.map((member: OktaUserGroupMember) => (
              <TableRow key={member.active_user?.id}>
                <TableCell>
                  <Link
                    to={`/users/${member.active_user?.email.toLowerCase()}`}
                    sx={{
                      textDecoration: 'none',
                      color: 'inherit',
                    }}
                    component={RouterLink}>
                    {displayUserName(member.active_user)}
                  </Link>
                </TableCell>
                <TableCell>
                  <Link
                    to={`/users/${member.active_user?.email.toLowerCase()}`}
                    sx={{
                      textDecoration: 'none',
                      color: 'inherit',
                    }}
                    component={RouterLink}>
                    {member.active_user?.email.toLowerCase()}
                  </Link>
                </TableCell>
                <TableCell>
                  <Ending memberships={member_list} />
                </TableCell>
              </TableRow>
            ))
          ) : (
            <EmptyListEntry />
          )}
        </TableBody>

        <TableFooter>
          <TableRow />
        </TableFooter>
      </Table>
    </TableContainer>
  );
};

export const AppsAccordionListGroup: React.FC<AppAccordionListGroupProps> = ({app_group}) => {
  const [groupExpanded, setGroupExpanded] = React.useState<boolean>(false);
  const [memberExpanded, setMemberExpanded] = React.useState<boolean>(false);

  const handleChange = (panel: string) => (event: React.SyntheticEvent, newExpanded: boolean) => {
    switch (panel) {
      case 'members':
        setMemberExpanded(!memberExpanded);
        break;
      case 'owners':
        setGroupExpanded(!groupExpanded);
        break;
      default:
        break;
    }
  };
  return (
    <React.Fragment>
      {app_group &&
        app_group?.map((appGroup) => (
          <Grid key={appGroup.id} item xs={12}>
            <TableContainer component={Paper}>
              <Table sx={{minWidth: 325}} aria-label="app group owners">
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
                    <TableCell colSpan={3}>
                      <Stack direction="column" spacing={1}>
                        <Accordion expanded={groupExpanded} onChange={handleChange('owners')}>
                          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                            <Typography gutterBottom variant="body1" component="span">
                              Group Owners
                            </Typography>
                          </AccordionSummary>
                          <AccordionDetails>
                            <GroupDetailList member_list={appGroup.active_user_ownerships || []} />
                          </AccordionDetails>
                        </Accordion>
                        <Accordion expanded={memberExpanded} onChange={handleChange('members')}>
                          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                            <Typography gutterBottom variant="body1" component="span">
                              Group Members
                            </Typography>
                          </AccordionSummary>
                          <AccordionDetails>
                            <GroupDetailList member_list={appGroup.active_user_memberships || []} />
                          </AccordionDetails>
                        </Accordion>
                      </Stack>
                    </TableCell>
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
