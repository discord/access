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

const GroupDetailList: React.FC<GroupDetailListProps> = React.memo(({member_list, title}) => {
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
});

const AccordionItem: React.FC<{
  appGroup: AppGroup;
  expanded: boolean;
  onToggle: (id: string) => (event: React.SyntheticEvent, newExpanded: boolean) => void;
}> = React.memo(({appGroup, expanded, onToggle}) => {
  const {owners, members} = React.useMemo(() => {
    const owners = Object.entries(groupMemberships(appGroup.active_user_ownerships))
      .sort(sortGroupMembers)
      .map((memberList) => memberList[1][0]);

    const members = Object.entries(groupMemberships(appGroup.active_user_memberships))
      .sort(sortGroupMembers)
      .map((memberList) => memberList[1][0]);

    return {owners, members};
  }, [appGroup.active_user_ownerships, appGroup.active_user_memberships]);

  return (
    <TableContainer key={appGroup.id} component={Paper}>
      <Accordion expanded={expanded} onChange={onToggle(appGroup.name)}>
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
                  <Stack direction="row" useFlexGap flexWrap={'wrap'} justifyContent={'space-between'} gap={'2rem'}>
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
});

interface AppAccordionListGroupProps {
  app_group: AppGroup[];
  list_group_title?: string;
  list_group_description?: string;
  isExpanded?: boolean;
}

export const AppsAccordionListGroup: React.FC<AppAccordionListGroupProps> = React.memo(
  ({app_group, list_group_title, list_group_description, isExpanded = false}) => {
    // Optimize state management to avoid recreating the entire object
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

    React.useEffect(() => {
      if (app_group) {
        setExpanded((prevExpanded) => {
          const newExpanded = {...prevExpanded};
          let hasChanges = false;

          app_group.forEach((group) => {
            if (newExpanded[group.name] !== isExpanded) {
              newExpanded[group.name] = isExpanded;
              hasChanges = true;
            }
          });

          return hasChanges ? newExpanded : prevExpanded;
        });
      }
    }, [isExpanded, app_group]);

    const handleChange = React.useCallback(
      (id: string) => (event: React.SyntheticEvent, newExpanded: boolean) => {
        setExpanded((prev) => ({...prev, [id]: newExpanded}));
      },
      [],
    );

    const appGroupList = React.useMemo(() => {
      return app_group || [];
    }, [app_group]);

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
          {(appGroupList?.length || 0) > 0 && list_group_title && (
            <Typography variant="h5" component={'div'} fontWeight={500}>
              {list_group_title}
            </Typography>
          )}
          {(appGroupList?.length || 0) > 0 && list_group_description && (
            <Typography variant="body1" component={'div'}>
              {list_group_description}
            </Typography>
          )}
          {appGroupList.map((appGroup) => (
            <AccordionItem
              key={appGroup.id}
              appGroup={appGroup}
              expanded={expanded[appGroup.name] || false}
              onToggle={handleChange}
            />
          ))}
        </Grid>
      </React.Fragment>
    );
  },
);
