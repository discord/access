import React from 'react';
import {Link as RouterLink, useParams, useNavigate} from 'react-router-dom';

import Box from '@mui/material/Box';
import Chip from '@mui/material/Chip';
import Container from '@mui/material/Container';
import Divider from '@mui/material/Divider';
import Grid from '@mui/material/Grid';
import IconButton from '@mui/material/IconButton';
import Link from '@mui/material/Link';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TableFooter from '@mui/material/TableFooter';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';

import AuditGroupIcon from '@mui/icons-material/History';
import AuditRoleIcon from '@mui/icons-material/Diversity2';
import DeleteIcon from '@mui/icons-material/Close';
import GroupIcon from '@mui/icons-material/People';
import TagIcon from '@mui/icons-material/LocalOffer';

import {useCurrentUser} from '../../authentication';
import ChangeTitle from '../../tab-title';
import CreateUpdateGroup from './CreateUpdate';
import DeleteGroup from './Delete';
import AddUsers from './AddUsers';
import AddRoles from './AddRoles';
import ExternallyManaged from './ExternallyManaged';
import AddGroups from '../roles/AddGroups';
import CreateRequest from '../requests/Create';
import RemoveGroupsDialog, {RemoveGroupsDialogParameters} from '../roles/RemoveGroups';
import RemoveOwnDirectAccessDialog, {RemoveOwnDirectAccessDialogParameters} from './RemoveOwnDirectAccess';
import NotFound from '../NotFound';
import Loading from '../../components/Loading';
import Ending from '../../components/Ending';
import {groupBy, displayGroupType, displayUserName} from '../../helpers';
import {useGetAppById, useGetGroupById, usePutGroupMembersById} from '../../api/apiComponents';
import {
  App,
  PolymorphicGroup,
  OktaUserGroupMember,
  RoleGroupMap,
  AppGroup,
  RoleGroup,
  GroupMember,
} from '../../api/apiSchemas';
import {canManageGroup} from '../../authorization';
import {EmptyListEntry} from '../../components/EmptyListEntry';
import {Diversity3 as RoleIcon} from '@mui/icons-material';
import AppLinkButton from './AppLinkButton';
import AvatarButton from '../../components/AvatarButton';
import MembershipChip from '../../components/MembershipChip';

function sortGroupMembers(
  [aUserId, aUsers]: [string, Array<OktaUserGroupMember>],
  [bUserId, bUsers]: [string, Array<OktaUserGroupMember>],
): number {
  // compare using the email of the user for the membership list
  let aEmail = aUsers[0].active_user?.email ?? '';
  let bEmail = bUsers[0].active_user?.email ?? '';
  return aEmail.localeCompare(bEmail);
}

function sortOktaUserGroupMembers(aMember: OktaUserGroupMember, bMember: OktaUserGroupMember): number {
  // compare using the name of the active role group, treating no active role group as the first element
  let aName = aMember.active_role_group_mapping?.active_role_group?.name ?? '';
  let bName = bMember.active_role_group_mapping?.active_role_group?.name ?? '';
  return aName.localeCompare(bName);
}

function sortRoleGroups(
  [aGroupId, aGroups]: [string, Array<RoleGroupMap>],
  [bGroupId, bGroups]: [string, Array<RoleGroupMap>],
): number {
  let aName = aGroups[0].active_group?.name ?? '';
  let bName = bGroups[0].active_group?.name ?? '';
  return aName.localeCompare(bName);
}

export default function ReadGroup() {
  const currentUser = useCurrentUser();

  const {id} = useParams();
  const navigate = useNavigate();

  const [removeGroupsFromRoleDialogOpen, setRemoveGroupsFromRoleDialogOpen] = React.useState(false);
  const [removeGroupsFromRoleDialogParameters, setRemoveGroupsFromRoleDialogParameters] =
    React.useState<RemoveGroupsDialogParameters>({} as RemoveGroupsDialogParameters);

  const [removeOwnDirectAccessDialogOpen, setRemoveOwnDirectAccessDialogOpen] = React.useState(false);
  const [removeOwnDirectAccessDialogParameters, setRemoveOwnDirectAccessDialogParameters] =
    React.useState<RemoveOwnDirectAccessDialogParameters>({} as RemoveOwnDirectAccessDialogParameters);

  const {data, isError, isLoading} = useGetGroupById({
    pathParams: {groupId: id ?? ''},
  });

  const group = data ?? ({} as PolymorphicGroup);

  const {data: appData} = useGetAppById(
    {
      pathParams: {
        appId: ((group ?? {}) as AppGroup).app?.id ?? '',
      },
    },
    {
      enabled: group.type == 'app_group',
    },
  );

  const app = appData ?? ({} as App);

  const putGroupUsers = usePutGroupMembersById({
    onSuccess: () => navigate(0),
  });

  if (isError) {
    return <NotFound />;
  }

  if (isLoading) {
    return <Loading />;
  }

  const appOwnershipsArray = (app.active_owner_app_groups ?? [])
    .map((appGroup) => appGroup.active_user_ownerships ?? [])
    .flat();
  // set of app owner ids
  const appOwnershipSet: Set<string> = appOwnershipsArray.reduce((out, user) => {
    out.add(user.active_user!.id);
    return out;
  }, new Set<string>());

  const directRoleOwnerships: Set<string> = (group.active_user_ownerships ?? []).reduce((out, user) => {
    out.add(user.active_user!.id);
    return out;
  }, new Set<string>());

  let allOwnerships: Set<OktaUserGroupMember> = new Set(group.active_user_ownerships ?? []);
  appOwnershipsArray.forEach((user) => {
    directRoleOwnerships.has(user.active_user!.id) ? null : allOwnerships.add(user);
  });

  let ownerships = groupBy(Array.from(allOwnerships), (m) => m.active_user?.id);

  const memberships = groupBy(group.active_user_memberships, (m) => m.active_user?.id);

  let role_associated_group_owners: Record<string, RoleGroupMap[]> = {};
  let role_associated_group_members: Record<string, RoleGroupMap[]> = {};
  if (group.type == 'role_group') {
    role_associated_group_owners = groupBy(
      (group as RoleGroup).active_role_associated_group_owner_mappings,
      (g) => g.active_group?.id,
    );
    role_associated_group_members = groupBy(
      (group as RoleGroup).active_role_associated_group_member_mappings,
      (g) => g.active_group?.id,
    );
  }

  const groupOwner = group.is_managed && canManageGroup(currentUser, group);

  const removeDirectUserFromGroup = (userId: string, owner: boolean) => {
    const groupUsers: GroupMember = {
      members_to_add: [],
      members_to_remove: [],
      owners_to_add: [],
      owners_to_remove: [],
    };

    if (owner) {
      groupUsers.owners_to_remove = [userId];
    } else {
      groupUsers.members_to_remove = [userId];
    }

    putGroupUsers.mutate({
      body: groupUsers,
      pathParams: {groupId: group.id ?? ''},
    });
  };

  const removeGroupFromRole = (removeGroup: PolymorphicGroup, fromRole: RoleGroup, owner: boolean) => {
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
      <ChangeTitle title={group.name} />
      <Container maxWidth="lg" sx={{my: 4}}>
        <Grid container spacing={3}>
          <Grid item sm={12}>
            <Paper sx={{p: 2}}>
              <Stack direction="column" gap={2}>
                <Stack direction={{sm: 'column', md: 'row-reverse'}} gap={1}>
                  <Stack direction={{sm: 'row', md: 'column'}} gap={2} justifyContent="center">
                    <AvatarButton
                      icon={group.type === 'role_group' ? <RoleIcon /> : <GroupIcon />}
                      text={displayGroupType(group)}
                    />
                    {group.type == 'app_group' && <AppLinkButton group={group as AppGroup} />}
                    {!group.is_managed && <ExternallyManaged group={group} />}
                  </Stack>
                  <Stack
                    alignItems="center"
                    justifyContent="center"
                    direction="column"
                    gap={1}
                    flexGrow={1}
                    sx={{wordBreak: 'break-word'}}
                    paddingLeft={{sm: 0, md: '100px'}}>
                    <Typography variant="h3" align="center">
                      {group.deleted_at != null ? (
                        <>
                          <s>{group.name}</s> is Deleted
                        </>
                      ) : (
                        group.name
                      )}
                    </Typography>
                    <Typography variant="h5" align="center">
                      {group.description}
                    </Typography>
                    <Box>
                      {group.active_group_tags?.map((tagMap) => (
                        <Chip
                          key={'tag' + tagMap.active_tag!.id}
                          label={tagMap.active_tag!.name}
                          color="primary"
                          onClick={() => navigate(`/tags/${tagMap.active_tag!.name}`)}
                          variant={tagMap.active_app_tag_mapping ? 'outlined' : 'filled'}
                          icon={<TagIcon />}
                          sx={{
                            margin: '10px 2px 0 2px',
                            bgcolor: (theme) =>
                              tagMap.active_tag!.enabled ? 'primary' : theme.palette.action.disabled,
                          }}
                        />
                      ))}
                    </Box>
                  </Stack>
                </Stack>
                <Divider />
                <Stack direction="row" justifyContent="center">
                  <Tooltip title="Edit" placement="top" PopperProps={moveTooltip}>
                    <div>
                      <CreateUpdateGroup currentUser={currentUser} group={group} />
                    </div>
                  </Tooltip>
                  <Tooltip title="Delete" placement="top" PopperProps={moveTooltip}>
                    <div>
                      <DeleteGroup currentUser={currentUser} group={group} />
                    </div>
                  </Tooltip>
                  <Tooltip
                    title={group.type == 'role_group' ? 'Users audit' : 'Audit'}
                    placement="top"
                    PopperProps={moveTooltip}>
                    <IconButton aria-label="audit" to={`/groups/${id}/audit`} component={RouterLink}>
                      <AuditGroupIcon />
                    </IconButton>
                  </Tooltip>
                  {group.type == 'role_group' ? (
                    <Tooltip title="Role audit" placement="top" PopperProps={moveTooltip}>
                      <IconButton aria-label="audit" to={`/roles/${id}/audit`} component={RouterLink}>
                        <AuditRoleIcon />
                      </IconButton>
                    </Tooltip>
                  ) : null}
                </Stack>
              </Stack>
            </Paper>
          </Grid>
          {group.type == 'role_group' ? (
            <>
              <Grid item xs={6}>
                <TableContainer component={Paper}>
                  <Table sx={{minWidth: 325}} size="small" aria-label="groups owned by role membership">
                    <TableHead>
                      <TableRow>
                        <TableCell>
                          <Typography variant="h6" color="text.accent">
                            Groups Owned by Role Membership
                          </Typography>
                        </TableCell>
                        <TableCell align="right" colSpan={2}>
                          <AddGroups currentUser={currentUser} group={group} owner={true} />
                        </TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell>Name</TableCell>
                        <TableCell colSpan={2}>
                          <Grid container>
                            <Grid item xs={2}>
                              Ending
                            </Grid>
                            <Grid item xs={10}>
                              <Box
                                sx={{
                                  display: 'flex',
                                  justifyContent: 'flex-end',
                                  alignItems: 'right',
                                }}>
                                <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                                Total: {Object.keys(role_associated_group_owners).length}
                              </Box>
                            </Grid>
                          </Grid>
                        </TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {Object.keys(role_associated_group_owners).length > 0 ? (
                        Object.entries(role_associated_group_owners)
                          .sort(sortRoleGroups)
                          .map(([groupId, groups]: [string, Array<RoleGroupMap>]) => (
                            <TableRow key={'roleownersgroups' + groupId}>
                              <TableCell>
                                <Link
                                  to={`/groups/${groups[0].active_group?.name}`}
                                  sx={{
                                    textDecoration: 'none',
                                    color: 'inherit',
                                    '&:hover': {
                                      color: (theme) => theme.palette.primary.main,
                                    },
                                  }}
                                  component={RouterLink}>
                                  {groups[0].active_group?.name}
                                </Link>
                              </TableCell>
                              <TableCell>
                                <Ending memberships={groups} />
                              </TableCell>
                              {groupOwner || canManageGroup(currentUser, groups[0].active_group) ? (
                                <TableCell>
                                  <IconButton
                                    size="small"
                                    onClick={() =>
                                      removeGroupFromRole(
                                        groups[0].active_group ?? ({} as PolymorphicGroup),
                                        group,
                                        true,
                                      )
                                    }>
                                    <DeleteIcon />
                                  </IconButton>
                                </TableCell>
                              ) : null}
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
              </Grid>
              <Grid item xs={6}>
                <TableContainer component={Paper}>
                  <Table sx={{minWidth: 325}} size="small" aria-label="groups with members granted by role membership">
                    <TableHead>
                      <TableRow>
                        <TableCell>
                          <Typography variant="h6" color="text.accent">
                            Groups with Members granted by Role Membership
                          </Typography>
                        </TableCell>
                        <TableCell align="right" colSpan={2}>
                          <AddGroups currentUser={currentUser} group={group} owner={false} />
                        </TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell>Name</TableCell>
                        <TableCell colSpan={2}>
                          <Grid container>
                            <Grid item xs={2}>
                              Ending
                            </Grid>
                            <Grid item xs={10}>
                              <Box
                                sx={{
                                  display: 'flex',
                                  justifyContent: 'flex-end',
                                  alignItems: 'right',
                                }}>
                                <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                                Total: {Object.keys(role_associated_group_members).length}
                              </Box>
                            </Grid>
                          </Grid>
                        </TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {Object.keys(role_associated_group_members).length > 0 ? (
                        Object.entries(role_associated_group_members)
                          .sort(sortRoleGroups)
                          .map(([groupId, groups]: [string, Array<RoleGroupMap>]) => (
                            <TableRow key={'rolemembergroup' + groupId}>
                              <TableCell>
                                <Link
                                  to={`/groups/${groups[0].active_group?.name}`}
                                  sx={{
                                    textDecoration: 'none',
                                    color: 'inherit',
                                    '&:hover': {
                                      color: (theme) => theme.palette.primary.main,
                                    },
                                  }}
                                  component={RouterLink}>
                                  {groups[0].active_group?.name}
                                </Link>
                              </TableCell>
                              <TableCell>
                                <Ending memberships={groups} />
                              </TableCell>
                              {groupOwner || canManageGroup(currentUser, groups[0].active_group) ? (
                                <TableCell>
                                  <IconButton
                                    size="small"
                                    onClick={() =>
                                      removeGroupFromRole(
                                        groups[0].active_group ?? ({} as PolymorphicGroup),
                                        group,
                                        false,
                                      )
                                    }>
                                    <DeleteIcon />
                                  </IconButton>
                                </TableCell>
                              ) : null}
                            </TableRow>
                          ))
                      ) : (
                        <TableRow key="rolemembergroup">
                          <TableCell>
                            <Typography variant="body2" color="text.secondary">
                              None
                            </Typography>
                          </TableCell>
                        </TableRow>
                      )}
                    </TableBody>
                    <TableFooter>
                      <TableRow />
                    </TableFooter>
                  </Table>
                </TableContainer>
              </Grid>
            </>
          ) : null}
          <Grid item xs={12}>
            <TableContainer component={Paper}>
              <Table sx={{minWidth: 650}} size="small" aria-label="group owners">
                <TableHead>
                  <TableRow>
                    <TableCell colSpan={3}>
                      <Stack direction="row" spacing={1} sx={{display: 'flex', alignItems: 'center'}}>
                        <Typography variant="h6" color="text.accent">
                          {group.type == 'role_group' ? 'Role Owners' : 'Group Owners'}
                        </Typography>
                        <Typography variant="body1" color="text.secondary">
                          {group.type == 'role_group'
                            ? 'Can manage description and membership of Role'
                            : 'Can manage description and membership of Group'}
                        </Typography>
                      </Stack>
                    </TableCell>
                    <TableCell align="right">
                      <Stack direction="row" spacing={1} justifyContent={'flex-end'}>
                        <CreateRequest currentUser={currentUser} group={group} owner={true}></CreateRequest>
                        <AddUsers currentUser={currentUser} group={group} owner={true} />
                        <AddRoles currentUser={currentUser} group={group} owner={true} />
                      </Stack>
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell>Name</TableCell>
                    <TableCell>Email</TableCell>
                    <TableCell>Ending</TableCell>
                    {group.type != 'role_group' ? (
                      <TableCell>
                        <Grid container>
                          <Grid item xs={6}>
                            {group.type == 'app_group' ? 'Direct, App Owner, or via Roles' : 'Direct or via Roles'}
                          </Grid>
                          <Grid item xs={6}>
                            <Box
                              sx={{
                                display: 'flex',
                                justifyContent: 'flex-end',
                                alignItems: 'right',
                              }}>
                              <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                              Total Owners: {Object.keys(ownerships).length}
                            </Box>
                          </Grid>
                        </Grid>
                      </TableCell>
                    ) : (
                      <TableCell>
                        <Box
                          sx={{
                            display: 'flex',
                            justifyContent: 'flex-end',
                            alignItems: 'right',
                          }}>
                          <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                          Total Owners: {Object.keys(ownerships).length}
                        </Box>
                      </TableCell>
                    )}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {Object.keys(ownerships).length > 0 ? (
                    Object.entries(ownerships)
                      .sort(sortGroupMembers)
                      .map(([userId, users]: [string, Array<OktaUserGroupMember>]) => (
                        <TableRow key={'owner' + userId}>
                          <TableCell>
                            <Link
                              to={`/users/${users[0].active_user?.email.toLowerCase()}`}
                              sx={{
                                textDecoration: 'none',
                                color: 'inherit',
                                '&:hover': {
                                  color: (theme) => theme.palette.primary.main,
                                },
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
                                '&:hover': {
                                  color: (theme) => theme.palette.primary.main,
                                },
                              }}
                              component={RouterLink}>
                              {users[0].active_user?.email.toLowerCase()}
                            </Link>
                          </TableCell>
                          <TableCell>
                            <Ending memberships={users} />
                          </TableCell>
                          {group.type != 'role_group' ? (
                            <TableCell>
                              <Stack direction="row" spacing={1}>
                                {appOwnershipSet.has(userId) &&
                                !(group.type == 'app_group' && (group as AppGroup).is_owner) ? (
                                  <Chip
                                    key={'owners' + userId + app.name}
                                    label={app.name}
                                    variant="outlined"
                                    color="primary"
                                    onClick={() => navigate(`/apps/${app.name}`)}
                                  />
                                ) : null}
                                {users.sort(sortOktaUserGroupMembers).map((user) =>
                                  directRoleOwnerships.has(user.active_user!.id) ? (
                                    <MembershipChip
                                      key={`${user.active_user?.id}${user.active_role_group_mapping?.active_role_group?.id}`}
                                      okta_user_group_member={user}
                                      group={group}
                                      removeRoleGroup={(roleGroup) => {
                                        removeGroupFromRole(group, roleGroup, true);
                                      }}
                                      removeDirectAccessAsUser={() => {
                                        removeOwnDirectAccess(userId, group, true);
                                      }}
                                      removeDirectAccessAsGroupManager={() => {
                                        removeDirectUserFromGroup(userId, true);
                                      }}
                                    />
                                  ) : null,
                                )}
                              </Stack>
                            </TableCell>
                          ) : (
                            <TableCell align="right">
                              {group.is_managed && (groupOwner || currentUser.id == userId) ? (
                                currentUser.id == userId ? (
                                  <IconButton size="small" onClick={() => removeOwnDirectAccess(userId, group, true)}>
                                    <DeleteIcon />
                                  </IconButton>
                                ) : (
                                  <IconButton
                                    size="small"
                                    onClick={() => removeDirectUserFromGroup(userId ?? '', true)}>
                                    <DeleteIcon />
                                  </IconButton>
                                )
                              ) : null}
                            </TableCell>
                          )}
                        </TableRow>
                      ))
                  ) : (
                    <TableRow key="owners">
                      <TableCell>
                        <Typography variant="body2" color="text.secondary">
                          None
                        </Typography>
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
                <TableFooter>
                  <TableRow />
                </TableFooter>
              </Table>
            </TableContainer>
          </Grid>
          <Grid item xs={12}>
            <TableContainer component={Paper}>
              <Table sx={{minWidth: 650}} size="small" aria-label="group members">
                <TableHead>
                  <TableRow>
                    <TableCell colSpan={3}>
                      <Stack direction="row" spacing={1} sx={{display: 'flex', alignItems: 'center'}}>
                        <Typography variant="h6" color="text.accent">
                          {group.type == 'role_group' ? 'Role Members' : 'Group Members'}
                        </Typography>
                        <Typography variant="body1" color="text.secondary">
                          {group.type == 'role_group' ? 'Members of Okta Group for Role' : 'Members of Okta Group'}
                        </Typography>
                      </Stack>
                    </TableCell>
                    <TableCell align="right">
                      <Stack direction="row" spacing={1} justifyContent={'flex-end'}>
                        <CreateRequest currentUser={currentUser} group={group} owner={false}></CreateRequest>
                        <AddUsers currentUser={currentUser} group={group} />
                        <AddRoles currentUser={currentUser} group={group} />
                      </Stack>
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell>Name</TableCell>
                    <TableCell>Email</TableCell>
                    <TableCell>Ending</TableCell>
                    {group.type != 'role_group' ? (
                      <TableCell>
                        <Grid container>
                          <Grid item xs={6}>
                            Direct or via Roles
                          </Grid>
                          <Grid item xs={6}>
                            <Box
                              sx={{
                                display: 'flex',
                                justifyContent: 'flex-end',
                                alignItems: 'right',
                              }}>
                              <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                              Total Members: {Object.keys(memberships).length}
                            </Box>
                          </Grid>
                        </Grid>
                      </TableCell>
                    ) : (
                      <TableCell>
                        <Box
                          sx={{
                            display: 'flex',
                            justifyContent: 'flex-end',
                            alignItems: 'right',
                          }}>
                          <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                          Total Members: {Object.keys(memberships).length}
                        </Box>
                      </TableCell>
                    )}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {Object.keys(memberships).length > 0 ? (
                    Object.entries(memberships)
                      .sort(sortGroupMembers)
                      .map(([userId, users]: [string, Array<OktaUserGroupMember>]) => (
                        <TableRow key={'member' + userId}>
                          <TableCell>
                            <Link
                              to={`/users/${users[0].active_user?.email.toLowerCase()}`}
                              sx={{
                                textDecoration: 'none',
                                color: 'inherit',
                                '&:hover': {
                                  color: (theme) => theme.palette.primary.main,
                                },
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
                                '&:hover': {
                                  color: (theme) => theme.palette.primary.main,
                                },
                              }}
                              component={RouterLink}>
                              {users[0].active_user?.email.toLowerCase()}
                            </Link>
                          </TableCell>
                          <TableCell>
                            <Ending memberships={users} />
                          </TableCell>
                          {group.type != 'role_group' ? (
                            <TableCell>
                              <Stack
                                direction="row"
                                spacing={1}
                                sx={{
                                  flexWrap: 'wrap',
                                  rowGap: '.5rem',
                                }}>
                                {users.sort(sortOktaUserGroupMembers).map((user) => (
                                  <MembershipChip
                                    key={`${user.active_user?.id}${user.active_role_group_mapping?.active_role_group?.id}`}
                                    okta_user_group_member={user}
                                    group={group}
                                    removeRoleGroup={(roleGroup) => {
                                      removeGroupFromRole(group, roleGroup, false);
                                    }}
                                    removeDirectAccessAsUser={() => {
                                      removeOwnDirectAccess(userId, group, false);
                                    }}
                                    removeDirectAccessAsGroupManager={() => {
                                      removeDirectUserFromGroup(userId, false);
                                    }}
                                  />
                                ))}
                              </Stack>
                            </TableCell>
                          ) : (
                            <TableCell align="right">
                              {group.is_managed && (groupOwner || currentUser.id == userId) ? (
                                currentUser.id == userId ? (
                                  <IconButton size="small" onClick={() => removeOwnDirectAccess(userId, group, false)}>
                                    <DeleteIcon />
                                  </IconButton>
                                ) : (
                                  <IconButton
                                    size="small"
                                    onClick={() => removeDirectUserFromGroup(userId ?? '', false)}>
                                    <DeleteIcon />
                                  </IconButton>
                                )
                              ) : null}
                            </TableCell>
                          )}
                        </TableRow>
                      ))
                  ) : (
                    <TableRow key="member">
                      <TableCell>
                        <Typography variant="body2" color="text.secondary">
                          None
                        </Typography>
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
                <TableFooter>
                  <TableRow />
                </TableFooter>
              </Table>
            </TableContainer>
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
