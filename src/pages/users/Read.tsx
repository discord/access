import React from 'react';
import {Link as RouterLink, useParams, useNavigate} from 'react-router-dom';

import AuditIcon from '@mui/icons-material/History';
import Box from '@mui/material/Box';
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
  onClickRemoveGroupFromRole: (removeGroup: PolymorphicGroup, fromRole: RoleGroup, owner: boolean) => void;
  onClickRemoveDirectAccess: (id: string, fromGroup: PolymorphicGroup, owner: boolean) => void;
}

function GroupTable({
  title,
  groups,
  user,
  owner,
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

  return (
    <TableContainer component={Paper}>
      <Table size="small" aria-label={title.toLowerCase()}>
        <TableHead>
          <TableRow>
            <TableCell colSpan={3}>
              <Typography variant="h6" color="text.accent">
                {title}
              </Typography>
            </TableCell>
          </TableRow>
          <TableRow>
            <TableCell>Name</TableCell>
            <TableCell>Ending</TableCell>
            <TableCell>Direct or via Roles</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {Object.keys(groups).length > 0 ? (
            Object.entries(groups)
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
  onClickRemoveGroupFromRole: (removeGroup: PolymorphicGroup, fromRole: RoleGroup, owner: boolean) => void;
  onClickRemoveDirectAccess: (id: string, fromGroup: PolymorphicGroup, owner: boolean) => void;
}

function SideBySideTables({
  ownerships,
  memberships,
  user,
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
          onClickRemoveGroupFromRole={onClickRemoveGroupFromRole}
          onClickRemoveDirectAccess={onClickRemoveDirectAccess}
        />
      </Grid>
    </Grid>
  );
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
                <Stack justifyContent="center" direction="row" gap={1}>
                  <Tooltip title="Audit" placement="top" PopperProps={moveTooltip}>
                    <IconButton
                      aria-label="audit"
                      to={`/users/${id}/audit`}
                      component={RouterLink}
                      sx={{
                        '&:hover': {
                          backgroundColor: 'primary.main',
                          color: 'primary.contrastText',
                        },
                      }}>
                      <AuditIcon />
                    </IconButton>
                  </Tooltip>
                </Stack>
              </Stack>
            </Paper>
          </Grid>
          <Grid item container xs={12} lg={3} spacing={3} alignContent={'flex-start'} order={{xs: 2, lg: 3}}>
            <Grid item xs={6} lg={12}>
              <ProfileToCard user={user} />
            </Grid>
            <Grid item xs={6} lg={12}>
              <ReportingToCard user={user} />
            </Grid>
          </Grid>
          <Grid item container xs={12} lg={9} rowSpacing={3} order={{xs: 3, lg: 2}}>
            {hasRoles && (
              <Grid item xs={12}>
                <Typography variant="h5" sx={{mb: 2}}>
                  Roles
                </Typography>
                <SideBySideTables
                  ownerships={ownerPartitions.roles}
                  memberships={memberPartitions.roles}
                  user={user}
                  onClickRemoveGroupFromRole={showRemoveGroupFromRoleDialog}
                  onClickRemoveDirectAccess={removeOwnDirectAccess}
                />
              </Grid>
            )}
            {hasAppGroups && (
              <Grid item xs={12}>
                <Typography variant="h5" sx={{mb: 2}}>
                  Apps
                </Typography>
                {allAppEntries.map((appEntry) => (
                  <Box key={appEntry.appId} sx={{mb: 3}}>
                    <Typography variant="h6" sx={{mb: 1}}>
                      <Link
                        to={`/apps/${appEntry.appName}`}
                        sx={{
                          textDecoration: 'none',
                          color: 'inherit',
                          '&:hover': {
                            color: (theme) => theme.palette.primary.main,
                          },
                        }}
                        component={RouterLink}>
                        {appEntry.appName}
                      </Link>
                    </Typography>
                    <SideBySideTables
                      ownerships={appEntry.ownerships}
                      memberships={appEntry.memberships}
                      user={user}
                      onClickRemoveGroupFromRole={showRemoveGroupFromRoleDialog}
                      onClickRemoveDirectAccess={removeOwnDirectAccess}
                    />
                  </Box>
                ))}
              </Grid>
            )}
            {hasStandardGroups && (
              <Grid item xs={12}>
                <Typography variant="h5" sx={{mb: 2}}>
                  Groups
                </Typography>
                <SideBySideTables
                  ownerships={ownerPartitions.standardGroups}
                  memberships={memberPartitions.standardGroups}
                  user={user}
                  onClickRemoveGroupFromRole={showRemoveGroupFromRoleDialog}
                  onClickRemoveDirectAccess={removeOwnDirectAccess}
                />
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
