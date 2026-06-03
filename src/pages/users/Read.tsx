import React from 'react';
import {Link as RouterLink, useParams, useNavigate} from 'react-router-dom';

import AuditIcon from '@mui/icons-material/History';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import UnfoldLessIcon from '@mui/icons-material/UnfoldLess';
import UnfoldMoreIcon from '@mui/icons-material/UnfoldMore';
import Accordion from '@mui/material/Accordion';
import AccordionDetails from '@mui/material/AccordionDetails';
import AccordionSummary from '@mui/material/AccordionSummary';
import Autocomplete from '@mui/material/Autocomplete';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Container from '@mui/material/Container';
import Divider from '@mui/material/Divider';
import Grid from '@mui/material/Grid';
import IconButton from '@mui/material/IconButton';
import Link from '@mui/material/Link';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemAvatar from '@mui/material/ListItemAvatar';
import ListItemText from '@mui/material/ListItemText';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableFooter from '@mui/material/TableFooter';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';

import {useCurrentUser} from '../../authentication';
import ChangeTitle from '../../tab-title';
import {useGetUserById, usePutGroupMembersById} from '../../api/apiComponents';
import {AppGroup, OktaUser, OktaUserGroupMember, PolymorphicGroup, RoleGroup} from '../../api/apiSchemas';
import UserAvatar from './UserAvatar';
import NotFound from '../NotFound';
import Ending from '../../components/Ending';
import Loading from '../../components/Loading';
import RemoveGroupsDialog, {RemoveGroupsDialogParameters} from '../roles/RemoveGroups';
import RemoveOwnDirectAccessDialog, {RemoveOwnDirectAccessDialogParameters} from '../groups/RemoveOwnDirectAccess';
import {groupBy, displayUserName} from '../../helpers';
import {canManageGroup, isGroupOwner} from '../../authorization';
import {EmptyListEntry} from '../../components/EmptyListEntry';
import MembershipChip from '../../components/MembershipChip';

function sortUserGroups(
  [aGroupId, aGroups]: [string, Array<OktaUserGroupMember>],
  [bGroupId, bGroups]: [string, Array<OktaUserGroupMember>],
): number {
  let aName = aGroups[0].active_group?.name ?? '';
  let bName = bGroups[0].active_group?.name ?? '';
  return aName.localeCompare(bName);
}

interface PartitionedGroups {
  roles: Record<string, OktaUserGroupMember[]>;
  appGroups: Record<string, OktaUserGroupMember[]>;
  standardGroups: Record<string, OktaUserGroupMember[]>;
}

function partitionByType(members: OktaUserGroupMember[] | undefined): PartitionedGroups {
  const roles: OktaUserGroupMember[] = [];
  const appGroups: OktaUserGroupMember[] = [];
  const standardGroups: OktaUserGroupMember[] = [];

  for (const m of members ?? []) {
    const type = m.active_group?.type;
    if (type === 'role_group') {
      roles.push(m);
    } else if (type === 'app_group') {
      appGroups.push(m);
    } else {
      standardGroups.push(m);
    }
  }

  return {
    roles: groupBy(roles, (m) => m.active_group?.id),
    appGroups: groupBy(appGroups, (m) => m.active_group?.id),
    standardGroups: groupBy(standardGroups, (m) => m.active_group?.id),
  };
}

interface AppSubGroup {
  appId: string;
  appName: string;
  groups: Record<string, OktaUserGroupMember[]>;
}

function groupByApp(appGroupsById: Record<string, OktaUserGroupMember[]>): AppSubGroup[] {
  const byApp: Record<string, AppSubGroup> = {};

  for (const [groupId, members] of Object.entries(appGroupsById)) {
    const appGroup = members[0].active_group as AppGroup;
    const appId = appGroup?.app?.id ?? '';
    const appName = appGroup?.app?.name ?? '';

    if (!byApp[appId]) {
      byApp[appId] = {appId, appName, groups: {}};
    }
    byApp[appId].groups[groupId] = members;
  }

  return Object.values(byApp).sort((a, b) => a.appName.localeCompare(b.appName));
}

interface ProfileToCardProps {
  user: OktaUser;
}

function ProfileToCard({user}: ProfileToCardProps) {
  if (user.profile == null) {
    return null;
  }
  return (
    <Card>
      <CardContent>
        <Typography gutterBottom variant="h6" component="div">
          Profile
        </Typography>
        <List>
          {Object.entries(user.profile)
            .filter(([u, v]) => v != null && u != 'Pronouns' && u != 'Name Pronunciation')
            .map(([key, value]: [string, string]) => (
              <ListItem key={key} sx={{padding: 0}}>
                <ListItemText primary={key} secondary={value} />
              </ListItem>
            ))}
        </List>
      </CardContent>
    </Card>
  );
}

interface ReportingToCardProps {
  user: OktaUser;
}

function ReportingToCard({user}: ReportingToCardProps) {
  return (
    <Card>
      <CardContent>
        <Typography gutterBottom variant="h6" component="div">
          Reporting to
        </Typography>
        <List>
          {user.manager ? (
            <ListItem
              component={RouterLink}
              to={`/users/${user.manager.email.toLowerCase()}`}
              sx={{
                textDecoration: 'none',
                color: 'inherit',
                padding: 0,
                '&:hover': {
                  backgroundColor: 'action.hover',
                  borderRadius: 1,
                },
              }}>
              <ListItemAvatar>
                <UserAvatar name={displayUserName(user.manager)} size={32} variant={'body1'} />
              </ListItemAvatar>
              <ListItemText primary={displayUserName(user.manager)} secondary={user.manager.profile?.Title ?? '—'} />
            </ListItem>
          ) : (
            <ListItem alignItems="flex-start" sx={{padding: 0}}>
              <ListItemText primary="No-one" />
            </ListItem>
          )}
        </List>
      </CardContent>
    </Card>
  );
}

interface GroupTableProps {
  title: string;
  groups: Record<string, OktaUserGroupMember[]>;
  user: OktaUser;
  owner: boolean;
  filterGroupName?: string | null;
  onClickRemoveGroupFromRole: (removeGroup: PolymorphicGroup, fromRole: RoleGroup, owner: boolean) => void;
  onClickRemoveDirectAccess: (id: string, fromGroup: PolymorphicGroup, owner: boolean) => void;
}

function GroupTable({
  title,
  groups,
  user,
  owner,
  filterGroupName,
  onClickRemoveGroupFromRole,
  onClickRemoveDirectAccess,
}: GroupTableProps) {
  const navigate = useNavigate();

  const putGroupUsers = usePutGroupMembersById({
    onSuccess: () => navigate(0),
  });

  const removeUserFromGroup = React.useCallback(
    (groupId: string) => {
      putGroupUsers.mutate({
        body: owner
          ? {owners_to_remove: [user.id], members_to_add: [], members_to_remove: [], owners_to_add: []}
          : {members_to_remove: [user.id], members_to_add: [], owners_to_remove: [], owners_to_add: []},
        pathParams: {groupId},
      });
    },
    [putGroupUsers, owner, user.id],
  );

  const filterQuery = filterGroupName?.trim().toLowerCase() ?? '';
  const filteredEntries = Object.entries(groups).filter(([_, members]) => {
    if (!filterQuery) return true;
    return (members[0]?.active_group?.name ?? '').toLowerCase().includes(filterQuery);
  });

  return (
    <TableContainer component={Paper}>
      <Table size="small" aria-label={title.toLowerCase()}>
        <TableHead>
          <TableRow>
            <TableCell colSpan={3}>
              <Box sx={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
                <Typography variant="h6" color="text.accent">
                  {title}
                </Typography>
                <Box sx={{display: 'flex', alignItems: 'center'}}>
                  <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                  Total: {filteredEntries.length}
                </Box>
              </Box>
            </TableCell>
          </TableRow>
          <TableRow>
            <TableCell>Name</TableCell>
            <TableCell>Ending</TableCell>
            <TableCell>Direct or via Roles</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {filteredEntries.length > 0 ? (
            filteredEntries
              .sort(sortUserGroups)
              .map(([groupId, groupMembers]: [string, Array<OktaUserGroupMember>]) => (
                <TableRow key={groupId}>
                  <TableCell>
                    <Link
                      to={`/groups/${groupMembers[0].active_group?.name}`}
                      sx={{
                        textDecoration: 'none',
                        color: 'inherit',
                        '&:hover': {
                          color: (theme) => theme.palette.primary.main,
                        },
                      }}
                      component={RouterLink}>
                      {groupMembers[0].active_group?.name}
                    </Link>
                  </TableCell>
                  <TableCell>
                    <Ending memberships={groupMembers} />
                  </TableCell>
                  <TableCell>
                    <Stack
                      direction="row"
                      spacing={1}
                      sx={{
                        flexWrap: 'wrap',
                        rowGap: '.5rem',
                      }}>
                      {groupMembers.map((group) =>
                        group.active_group ? (
                          <MembershipChip
                            key={group.active_role_group_mapping?.active_role_group?.name ?? ''}
                            okta_user_group_member={group}
                            group={group.active_group}
                            removeRoleGroup={(roleGroup) => {
                              onClickRemoveGroupFromRole(group.active_group!, roleGroup, owner);
                            }}
                            removeDirectAccessAsUser={() => {
                              onClickRemoveDirectAccess(user.id, group.active_group!, owner);
                            }}
                            removeDirectAccessAsGroupManager={() => {
                              removeUserFromGroup(group.active_group!.id ?? '');
                            }}
                          />
                        ) : null,
                      )}
                    </Stack>
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
  );
}

interface SideBySideTablesProps {
  ownerships: Record<string, OktaUserGroupMember[]>;
  memberships: Record<string, OktaUserGroupMember[]>;
  user: OktaUser;
  filterGroupName?: string | null;
  onClickRemoveGroupFromRole: (removeGroup: PolymorphicGroup, fromRole: RoleGroup, owner: boolean) => void;
  onClickRemoveDirectAccess: (id: string, fromGroup: PolymorphicGroup, owner: boolean) => void;
}

function SideBySideTables({
  ownerships,
  memberships,
  user,
  filterGroupName,
  onClickRemoveGroupFromRole,
  onClickRemoveDirectAccess,
}: SideBySideTablesProps) {
  return (
    <Grid container spacing={2}>
      <Grid item xs={12} md={6}>
        <GroupTable
          title="Ownerships"
          groups={ownerships}
          user={user}
          owner={true}
          filterGroupName={filterGroupName}
          onClickRemoveGroupFromRole={onClickRemoveGroupFromRole}
          onClickRemoveDirectAccess={onClickRemoveDirectAccess}
        />
      </Grid>
      <Grid item xs={12} md={6}>
        <GroupTable
          title="Memberships"
          groups={memberships}
          user={user}
          owner={false}
          filterGroupName={filterGroupName}
          onClickRemoveGroupFromRole={onClickRemoveGroupFromRole}
          onClickRemoveDirectAccess={onClickRemoveDirectAccess}
        />
      </Grid>
    </Grid>
  );
}

interface CollapsibleSectionProps {
  summaryLeft: React.ReactNode;
  ownerships: Record<string, OktaUserGroupMember[]>;
  memberships: Record<string, OktaUserGroupMember[]>;
  user: OktaUser;
  filterGroupName?: string | null;
  expanded: boolean;
  onToggle: (event: React.SyntheticEvent, expanded: boolean) => void;
  onClickRemoveGroupFromRole: (removeGroup: PolymorphicGroup, fromRole: RoleGroup, owner: boolean) => void;
  onClickRemoveDirectAccess: (id: string, fromGroup: PolymorphicGroup, owner: boolean) => void;
}

function CollapsibleSection({
  summaryLeft,
  ownerships,
  memberships,
  user,
  filterGroupName,
  expanded,
  onToggle,
  onClickRemoveGroupFromRole,
  onClickRemoveDirectAccess,
}: CollapsibleSectionProps) {
  const ownerCount = Object.keys(ownerships).length;
  const memberCount = Object.keys(memberships).length;

  return (
    <Accordion expanded={expanded} onChange={onToggle} TransitionProps={{unmountOnExit: true}}>
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Box sx={{display: 'inline-flex', flexGrow: 1, alignItems: 'center'}}>
          <Box sx={{flexGrow: 0.95}}>{summaryLeft}</Box>
          <Box sx={{display: 'flex', alignItems: 'center', justifyContent: 'flex-end'}}>
            <Divider sx={{mx: 2}} orientation="vertical" flexItem />
            <Box>
              Ownerships: {ownerCount}
              <br />
              Memberships: {memberCount}
            </Box>
          </Box>
        </Box>
      </AccordionSummary>
      <AccordionDetails>
        <SideBySideTables
          ownerships={ownerships}
          memberships={memberships}
          user={user}
          filterGroupName={filterGroupName}
          onClickRemoveGroupFromRole={onClickRemoveGroupFromRole}
          onClickRemoveDirectAccess={onClickRemoveDirectAccess}
        />
      </AccordionDetails>
    </Accordion>
  );
}

function sectionContainsGroupName(
  ownerships: Record<string, OktaUserGroupMember[]>,
  memberships: Record<string, OktaUserGroupMember[]>,
  groupName: string | null,
): boolean {
  const q = groupName?.trim().toLowerCase() ?? '';
  if (!q) return false;
  const all = [...Object.values(ownerships), ...Object.values(memberships)];
  return all.some((members) => (members[0]?.active_group?.name ?? '').toLowerCase().includes(q));
}

function collectGroupNames(ownerPartitions: PartitionedGroups, memberPartitions: PartitionedGroups): string[] {
  const names = new Set<string>();
  const visit = (groupsById: Record<string, OktaUserGroupMember[]>) => {
    for (const members of Object.values(groupsById)) {
      const name = members[0]?.active_group?.name;
      if (name) names.add(name);
    }
  };
  visit(ownerPartitions.roles);
  visit(ownerPartitions.appGroups);
  visit(ownerPartitions.standardGroups);
  visit(memberPartitions.roles);
  visit(memberPartitions.appGroups);
  visit(memberPartitions.standardGroups);
  return Array.from(names).sort((a, b) => a.localeCompare(b));
}

export default function ReadUser() {
  const currentUser = useCurrentUser();

  const {id} = useParams();

  const [removeGroupsFromRoleDialogOpen, setRemoveGroupsFromRoleDialogOpen] = React.useState(false);
  const [removeGroupsFromRoleDialogParameters, setRemoveGroupsFromRoleDialogParameters] =
    React.useState<RemoveGroupsDialogParameters>({} as RemoveGroupsDialogParameters);

  const [removeOwnDirectAccessDialogOpen, setRemoveOwnDirectAccessDialogOpen] = React.useState(false);
  const [removeOwnDirectAccessDialogParameters, setRemoveOwnDirectAccessDialogParameters] =
    React.useState<RemoveOwnDirectAccessDialogParameters>({} as RemoveOwnDirectAccessDialogParameters);

  const [searchSelection, setSearchSelection] = React.useState<string | null>(null);
  const [isExpandedAll, setIsExpandedAll] = React.useState(false);
  const [userToggleMap, setUserToggleMap] = React.useState<Record<string, boolean>>({});

  const {data, isError, isLoading} = useGetUserById({
    pathParams: {userId: id ?? ''},
  });

  if (isError) {
    return <NotFound />;
  }

  if (isLoading) {
    return <Loading />;
  }

  const user = data ?? ({} as OktaUser);

  const ownerPartitions = partitionByType(user.active_group_ownerships);
  const memberPartitions = partitionByType(user.active_group_memberships);
  const ownerAppsByApp = groupByApp(ownerPartitions.appGroups);
  const memberAppsByApp = groupByApp(memberPartitions.appGroups);

  const appMap = new Map<
    string,
    {
      appId: string;
      appName: string;
      ownerships: Record<string, OktaUserGroupMember[]>;
      memberships: Record<string, OktaUserGroupMember[]>;
    }
  >();
  for (const entry of ownerAppsByApp) {
    appMap.set(entry.appId, {appId: entry.appId, appName: entry.appName, ownerships: entry.groups, memberships: {}});
  }
  for (const entry of memberAppsByApp) {
    const existing = appMap.get(entry.appId);
    if (existing) {
      existing.memberships = entry.groups;
    } else {
      appMap.set(entry.appId, {appId: entry.appId, appName: entry.appName, ownerships: {}, memberships: entry.groups});
    }
  }
  const allAppEntries = Array.from(appMap.values()).sort((a, b) => a.appName.localeCompare(b.appName));

  const hasRoles = Object.keys(ownerPartitions.roles).length > 0 || Object.keys(memberPartitions.roles).length > 0;
  const hasAppGroups = allAppEntries.length > 0;
  const hasStandardGroups =
    Object.keys(ownerPartitions.standardGroups).length > 0 || Object.keys(memberPartitions.standardGroups).length > 0;

  const searchActive = !!searchSelection?.trim();
  const rolesHasMatch =
    !searchActive || sectionContainsGroupName(ownerPartitions.roles, memberPartitions.roles, searchSelection);
  const standardGroupsHasMatch =
    !searchActive ||
    sectionContainsGroupName(ownerPartitions.standardGroups, memberPartitions.standardGroups, searchSelection);
  const filteredAppEntries = searchActive
    ? allAppEntries.filter((entry) => sectionContainsGroupName(entry.ownerships, entry.memberships, searchSelection))
    : allAppEntries;

  const showRemoveGroupFromRoleDialog = (removeGroup: PolymorphicGroup, fromRole: RoleGroup, owner: boolean) => {
    setRemoveGroupsFromRoleDialogParameters({
      group: removeGroup,
      role: fromRole,
      owner: owner,
    });
    setRemoveGroupsFromRoleDialogOpen(true);
  };

  const removeOwnDirectAccess = (id: string, fromGroup: PolymorphicGroup, owner: boolean) => {
    setRemoveOwnDirectAccessDialogParameters({
      userId: id,
      group: fromGroup,
      owner: owner,
    });
    setRemoveOwnDirectAccessDialogOpen(true);
  };

  const allGroupNames = collectGroupNames(ownerPartitions, memberPartitions);

  const accordionExpanded = (id: string, hasMatch: boolean): boolean => {
    if (id in userToggleMap) return userToggleMap[id];
    if (searchSelection && hasMatch) return true;
    return isExpandedAll;
  };

  const handleAccordionToggle =
    (id: string) =>
    (_: React.SyntheticEvent, expanded: boolean): void => {
      setUserToggleMap((prev) => ({...prev, [id]: expanded}));
    };

  const handleToggleExpandAll = (): void => {
    setIsExpandedAll((prev) => !prev);
    setUserToggleMap({});
  };

  const handleSearchChange = (_: React.SyntheticEvent, value: string | null): void => {
    setSearchSelection(value);
    setUserToggleMap({});
  };

  const moveTooltip = {modifiers: [{name: 'offset', options: {offset: [0, -10]}}]};

  return (
    <React.Fragment>
      <ChangeTitle title={displayUserName(user)} />
      <Container maxWidth="xl" sx={{my: 4}}>
        <Grid container spacing={3}>
          <Grid item xs={12} alignItems="center">
            <Paper sx={{p: 2}}>
              <Stack direction="column" gap={2}>
                <Stack alignItems="center" direction="column" gap={1} sx={{wordBreak: 'break-word'}}>
                  <UserAvatar name={displayUserName(user)} size={100} variant={'h3'} />
                  <Typography variant="h3" textAlign="center">
                    {user.deleted_at != null ? (
                      <>
                        <s>{displayUserName(user)}</s> is Deleted
                      </>
                    ) : (
                      displayUserName(user)
                    )}
                  </Typography>
                  <Typography variant="h5" textAlign="center">
                    {user.email?.toLowerCase()}
                  </Typography>
                  <Typography>
                    {user.profile?.Pronouns}
                    {user.profile?.Pronouns && user.profile?.['Name Pronunciation'] && <> • </>}
                    {user.profile?.['Name Pronunciation']}
                  </Typography>
                </Stack>
                <Divider />
                <Box
                  sx={{
                    display: 'flex',
                    flexWrap: 'wrap',
                    alignItems: 'center',
                    gap: 2,
                  }}>
                  {(hasRoles || hasAppGroups || hasStandardGroups) && (
                    <>
                      <Autocomplete
                        size="small"
                        freeSolo
                        sx={{flex: '1 1 220px', minWidth: 0, maxWidth: 360}}
                        renderInput={(params) => <TextField {...params} label="Search Roles, Groups, and App Groups" />}
                        options={allGroupNames}
                        value={searchSelection}
                        onChange={handleSearchChange}
                        clearOnEscape
                      />
                      <Button
                        variant="contained"
                        color="primary"
                        size="small"
                        startIcon={isExpandedAll ? <UnfoldLessIcon /> : <UnfoldMoreIcon />}
                        onClick={handleToggleExpandAll}
                        sx={{flexShrink: 0}}>
                        {isExpandedAll ? 'Collapse' : 'Expand'}
                      </Button>
                    </>
                  )}
                  <Tooltip title="Audit" placement="top" PopperProps={moveTooltip}>
                    <IconButton
                      aria-label="audit"
                      to={`/users/${id}/audit`}
                      component={RouterLink}
                      sx={{
                        marginLeft: 'auto',
                        flexShrink: 0,
                        '&:hover': {
                          backgroundColor: 'primary.main',
                          color: 'primary.contrastText',
                        },
                      }}>
                      <AuditIcon />
                    </IconButton>
                  </Tooltip>
                </Box>
              </Stack>
            </Paper>
          </Grid>
          <Grid item container xs={12} lg={3} spacing={3} alignContent={'flex-start'} order={{xs: 2, lg: 3}}>
            <Grid item xs={12} sm={6} lg={12}>
              <ProfileToCard user={user} />
            </Grid>
            <Grid item xs={12} sm={6} lg={12}>
              <ReportingToCard user={user} />
            </Grid>
          </Grid>
          <Grid item container xs={12} lg={9} rowSpacing={3} order={{xs: 3, lg: 2}}>
            {hasRoles && rolesHasMatch && (
              <Grid item xs={12}>
                <Typography variant="h5" sx={{mb: 2}}>
                  Roles
                </Typography>
                <CollapsibleSection
                  summaryLeft={
                    <Typography variant="h6" color="text.accent">
                      All Roles
                    </Typography>
                  }
                  ownerships={ownerPartitions.roles}
                  memberships={memberPartitions.roles}
                  user={user}
                  filterGroupName={searchSelection}
                  expanded={accordionExpanded(
                    'roles',
                    sectionContainsGroupName(ownerPartitions.roles, memberPartitions.roles, searchSelection),
                  )}
                  onToggle={handleAccordionToggle('roles')}
                  onClickRemoveGroupFromRole={showRemoveGroupFromRoleDialog}
                  onClickRemoveDirectAccess={removeOwnDirectAccess}
                />
              </Grid>
            )}
            {hasStandardGroups && standardGroupsHasMatch && (
              <Grid item xs={12}>
                <Typography variant="h5" sx={{mb: 2}}>
                  Groups
                </Typography>
                <CollapsibleSection
                  summaryLeft={
                    <Typography variant="h6" color="text.accent">
                      All Groups
                    </Typography>
                  }
                  ownerships={ownerPartitions.standardGroups}
                  memberships={memberPartitions.standardGroups}
                  user={user}
                  filterGroupName={searchSelection}
                  expanded={accordionExpanded(
                    'groups',
                    sectionContainsGroupName(
                      ownerPartitions.standardGroups,
                      memberPartitions.standardGroups,
                      searchSelection,
                    ),
                  )}
                  onToggle={handleAccordionToggle('groups')}
                  onClickRemoveGroupFromRole={showRemoveGroupFromRoleDialog}
                  onClickRemoveDirectAccess={removeOwnDirectAccess}
                />
              </Grid>
            )}
            {hasAppGroups && filteredAppEntries.length > 0 && (
              <Grid item xs={12}>
                <Typography variant="h5" sx={{mb: 2}}>
                  Apps
                </Typography>
                <Stack spacing={2}>
                  {filteredAppEntries.map((appEntry) => {
                    const id = `app-${appEntry.appId}`;
                    const hasMatch = sectionContainsGroupName(
                      appEntry.ownerships,
                      appEntry.memberships,
                      searchSelection,
                    );
                    return (
                      <CollapsibleSection
                        key={appEntry.appId}
                        summaryLeft={
                          <Typography variant="h6" color="text.accent">
                            <Link
                              to={`/apps/${appEntry.appName}`}
                              sx={{
                                textDecoration: 'none',
                                color: 'inherit',
                                '&:hover': {
                                  color: (theme) => theme.palette.primary.main,
                                },
                              }}
                              component={RouterLink}
                              onClick={(e) => e.stopPropagation()}>
                              {appEntry.appName}
                            </Link>
                          </Typography>
                        }
                        ownerships={appEntry.ownerships}
                        memberships={appEntry.memberships}
                        user={user}
                        filterGroupName={searchSelection}
                        expanded={accordionExpanded(id, hasMatch)}
                        onToggle={handleAccordionToggle(id)}
                        onClickRemoveGroupFromRole={showRemoveGroupFromRoleDialog}
                        onClickRemoveDirectAccess={removeOwnDirectAccess}
                      />
                    );
                  })}
                </Stack>
              </Grid>
            )}
          </Grid>
        </Grid>
      </Container>
      {removeGroupsFromRoleDialogOpen ? (
        <RemoveGroupsDialog setOpen={setRemoveGroupsFromRoleDialogOpen} {...removeGroupsFromRoleDialogParameters} />
      ) : null}
      {removeOwnDirectAccessDialogOpen ? (
        <RemoveOwnDirectAccessDialog
          setOpen={setRemoveOwnDirectAccessDialogOpen}
          {...removeOwnDirectAccessDialogParameters}
        />
      ) : null}
    </React.Fragment>
  );
}
