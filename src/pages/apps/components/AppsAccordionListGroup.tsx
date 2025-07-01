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
import {displayUserName, groupBy, groupMemberships, sortGroupMembers} from '../../../helpers';
import {EmptyListEntry} from '../../../components/EmptyListEntry';
import Ending from '../../../components/Ending';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import {Link as RouterLink, useParams} from 'react-router-dom';

interface GroupDetailListProps {
  member_list: any[];
  title?: string;
}

const GroupDetailList: React.FC<GroupDetailListProps> = ({member_list, title}) => {
  return (
    <Stack direction="column" spacing={1}>
      {title && (
        <Typography variant="body1" component={'div'}>
          {title}
        </Typography>
      )}

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
              <EmptyListEntry cellProps={{colSpan: 3}} />
            )}
          </TableBody>

          <TableFooter>
            <TableRow />
          </TableFooter>
        </Table>
      </TableContainer>
    </Stack>
  );
};

interface AppAccordionListGroupProps {
  app_group: AppGroup[];
  list_group_title?: string;
  list_group_description?: string;
  isExpanded?: boolean;
}

export const AppsAccordionListGroup: React.FC<AppAccordionListGroupProps> = ({
  app_group,
  list_group_title,
  list_group_description,
  isExpanded = false,
}) => {
  const [expanded, setExpanded] = React.useState<Record<string, boolean>>(() => {
    if (isExpanded && app_group) {
      const initialExpanded: Record<string, boolean> = {};
      app_group.forEach((group) => {
        initialExpanded[group.name] = true;
      });
      return initialExpanded;
    }
    return {};
  });

  // Sync internal state with isExpanded prop changes
  React.useEffect(() => {
    if (app_group) {
      const newExpanded: Record<string, boolean> = {};
      app_group.forEach((group) => {
        newExpanded[group.name] = isExpanded;
      });
      setExpanded(newExpanded);
    }
  }, [isExpanded, app_group]);

  const handleChange = (id: string) => (event: React.SyntheticEvent, newExpanded: boolean) => {
    setExpanded({...expanded, [id]: newExpanded});
  };

  return (
    <React.Fragment>
      <Grid
        item
        xs={12}
        sx={{
          display: 'flex',
          flexDirection: 'column',
          gap: '1.25rem',
        }}>
        {(app_group?.length || 0) > 0 && list_group_title && (
          <Typography variant="h5" component={'div'} fontWeight={500}>
            {list_group_title}
          </Typography>
        )}
        {(app_group?.length || 0) > 0 && list_group_description && (
          <Typography variant="body1" component={'div'}>
            {list_group_description}
          </Typography>
        )}
        {app_group &&
          app_group?.map((appGroup) => {
            const owners = Object.entries(groupMemberships(appGroup.active_user_ownerships))
              .sort(sortGroupMembers)
              .map((memberList) => memberList[1][0]);

            const members = Object.entries(groupMemberships(appGroup.active_user_memberships))
              .sort(sortGroupMembers)
              .map((memberList) => memberList[1][0]);

            return (
              <TableContainer key={appGroup.id} component={Paper}>
                <Accordion expanded={expanded[appGroup.name] || false} onChange={handleChange(appGroup.name)}>
                  <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                    <Box
                      sx={{
                        display: 'inline-flex',
                        flexGrow: 1,
                      }}>
                      <Stack
                        direction="column"
                        spacing={1}
                        sx={{
                          flexGrow: 0.95,
                        }}>
                        <Typography variant="h6" color="text.accent">
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
                          {appGroup.description}
                        </Typography>
                      </Stack>
                      <Box
                        sx={{
                          display: 'flex',
                          justifyContent: 'flex-end',
                          alignItems: 'right',
                        }}>
                        <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                        Owners: {owners.length || 0} <br />
                        Members: {members.length || 0}
                      </Box>
                    </Box>
                  </AccordionSummary>
                  <AccordionDetails>
                    <Table aria-label="app group owners">
                      <TableBody className="accordion-body">
                        <TableRow>
                          <TableCell colSpan={2}>
                            <Stack
                              direction="row"
                              useFlexGap
                              flexWrap={'wrap'}
                              justifyContent={'space-between'}
                              gap={'2rem'}>
                              <GroupDetailList member_list={owners} title={'Group Owners'} />
                              <GroupDetailList member_list={members} title={'Members'} />
                            </Stack>
                          </TableCell>
                        </TableRow>
                      </TableBody>
                    </Table>
                  </AccordionDetails>
                </Accordion>
              </TableContainer>
            );
          })}
      </Grid>
    </React.Fragment>
  );
};
