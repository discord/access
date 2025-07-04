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

const GroupDetailList: React.FC<GroupDetailListProps> = React.memo(
  ({member_list, title}) => {
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
  },
  (prevProps, nextProps) => {
    return (
      prevProps.title === nextProps.title &&
      prevProps.member_list.length === nextProps.member_list.length &&
      prevProps.member_list.every(
        (member, index) => member.active_user?.id === nextProps.member_list[index]?.active_user?.id,
      )
    );
  },
);

const AccordionItem: React.FC<{
  appGroup: AppGroup;
  expanded: boolean;
  onToggle: (id: string) => (event: React.SyntheticEvent, newExpanded: boolean) => void;
}> = React.memo(
  ({appGroup, expanded, onToggle}) => {
    const {owners, members} = React.useMemo(() => {
      const owners = Object.entries(groupMemberships(appGroup.active_user_ownerships))
        .sort(sortGroupMembers)
        .map((memberList) => memberList[1][0]);

      const members = Object.entries(groupMemberships(appGroup.active_user_memberships))
        .sort(sortGroupMembers)
        .map((memberList) => memberList[1][0]);

      return {owners, members};
    }, [appGroup.active_user_ownerships, appGroup.active_user_memberships]);

    const handleToggle = React.useMemo(() => onToggle(appGroup.name), [onToggle, appGroup.name]);

    return (
      <TableContainer key={appGroup.id} component={Paper}>
        <Accordion expanded={expanded} onChange={handleToggle}>
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
  },
  (prevProps, nextProps) => {
    return (
      prevProps.expanded === nextProps.expanded &&
      prevProps.appGroup.id === nextProps.appGroup.id &&
      prevProps.appGroup.name === nextProps.appGroup.name &&
      prevProps.appGroup.description === nextProps.appGroup.description &&
      prevProps.appGroup.active_user_ownerships === nextProps.appGroup.active_user_ownerships &&
      prevProps.appGroup.active_user_memberships === nextProps.appGroup.active_user_memberships
    );
  },
);

interface AppAccordionListGroupProps {
  app_group: AppGroup[];
  list_group_title?: string;
  list_group_description?: string;
  isExpanded?: boolean;
}

export const AppsAccordionListGroup: React.FC<AppAccordionListGroupProps> = React.memo(
  ({app_group, list_group_title, list_group_description, isExpanded = false}) => {
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

    const appGroupRef = React.useRef(app_group);
    appGroupRef.current = app_group;

    React.useEffect(() => {
      const currentAppGroup = appGroupRef.current;
      if (currentAppGroup) {
        setExpanded((prevExpanded) => {
          const newExpanded = {...prevExpanded};
          let hasChanges = false;

          currentAppGroup.forEach((group) => {
            if (newExpanded[group.name] !== isExpanded) {
              newExpanded[group.name] = isExpanded;
              hasChanges = true;
            }
          });

          return hasChanges ? newExpanded : prevExpanded;
        });
      }
    }, [isExpanded]);

    const handleChange = React.useCallback(
      (id: string) => (event: React.SyntheticEvent, newExpanded: boolean) => {
        setExpanded((prev) => {
          if (prev[id] === newExpanded) {
            return prev;
          }
          return {...prev, [id]: newExpanded};
        });
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
  (prevProps, nextProps) => {
    return (
      prevProps.isExpanded === nextProps.isExpanded &&
      prevProps.list_group_title === nextProps.list_group_title &&
      prevProps.list_group_description === nextProps.list_group_description &&
      prevProps.app_group.length === nextProps.app_group.length &&
      prevProps.app_group.every(
        (group, index) =>
          group.id === nextProps.app_group[index]?.id &&
          group.name === nextProps.app_group[index]?.name &&
          group.description === nextProps.app_group[index]?.description &&
          group.active_user_ownerships === nextProps.app_group[index]?.active_user_ownerships &&
          group.active_user_memberships === nextProps.app_group[index]?.active_user_memberships,
      )
    );
  },
);
