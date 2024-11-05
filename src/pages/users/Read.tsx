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
import {useGetUserById, usePutGroupMembersById} from '../../api/apiComponents';
import {OktaUser, OktaUserGroupMember, PolymorphicGroup, RoleGroup} from '../../api/apiSchemas';
import UserAvatar from './UserAvatar';
import NotFound from '../NotFound';
import Ending from '../../components/Ending';
import Loading from '../../components/Loading';
import RemoveGroupsDialog, {RemoveGroupsDialogParameters} from '../roles/RemoveGroups';
import RemoveOwnDirectAccessDialog, {RemoveOwnDirectAccessDialogParameters} from '../groups/RemoveOwnDirectAccess';
import {groupBy, displayUserName, displayGroupType} from '../../helpers';
import {canManageGroup, isGroupOwner} from '../../authorization';

function sortUserGroups(
  [aGroupId, aGroups]: [string, Array<OktaUserGroupMember>],
  [bGroupId, bGroups]: [string, Array<OktaUserGroupMember>],
): number {
  let aName = aGroups[0].active_group?.name ?? '';
  let bName = bGroups[0].active_group?.name ?? '';
  return aName.localeCompare(bName);
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
            .filter(([_, v]) => v != null)
            .map(([key, value]: [string, string]) => (
              <ListItem key={key} sx={{paddingY: 0}}>
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
              sx={{textDecoration: 'none', color: 'inherit'}}>
              <ListItemAvatar>
                <UserAvatar name={displayUserName(user.manager)} size={32} variant={'body1'} />
              </ListItemAvatar>
              <ListItemText primary={displayUserName(user.manager)} secondary={user.manager.profile?.Title ?? '—'} />
            </ListItem>
          ) : (
            <ListItem alignItems="flex-start">
              <ListItemText primary="No-one" />
            </ListItem>
          )}
        </List>
      </CardContent>
    </Card>
  );
}

interface OwnerTableProps {
  user: OktaUser;
  ownerships: Record<string, OktaUserGroupMember[]>;
  onClickRemoveGroupFromRole: (removeGroup: PolymorphicGroup, fromRole: RoleGroup, owner: boolean) => void;
  onClickRemoveDirectAccess: (id: string, fromGroup: PolymorphicGroup, owner: boolean) => void;
}

function OwnerTable({user, ownerships, onClickRemoveGroupFromRole, onClickRemoveDirectAccess}: OwnerTableProps) {
  const currentUser = useCurrentUser();
  const navigate = useNavigate();

  const putGroupUsers = usePutGroupMembersById({
    onSuccess: () => navigate(0),
  });

  const removeUserFromGroup = React.useCallback(
    (groupId: string) => {
      putGroupUsers.mutate({
        body: {owners_to_remove: [user.id], members_to_add: [], members_to_remove: [], owners_to_add: []},
        pathParams: {groupId},
      });
    },
    [putGroupUsers],
  );

  return (
    <TableContainer component={Paper}>
      <Table sx={{minWidth: 650}} size="small" aria-label="owner of groups">
        <TableHead>
          <TableRow>
            <TableCell colSpan={3}>
              <Typography variant="h6" color="text.accent">
                Owner of Group or Roles
              </Typography>
            </TableCell>
            <TableCell>
              <Box
                sx={{
                  display: 'flex',
                  justifyContent: 'flex-end',
                  alignItems: 'right',
                }}>
                <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                Total Groups: {Object.keys(ownerships).length}
              </Box>
            </TableCell>
          </TableRow>
          <TableRow>
            <TableCell>Name</TableCell>
            <TableCell>Type</TableCell>
            <TableCell>Ending</TableCell>
            <TableCell>Direct or via Roles</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {Object.keys(ownerships).length > 0 ? (
            Object.entries(ownerships)
              .sort(sortUserGroups)
              .map(([groupId, groups]: [string, Array<OktaUserGroupMember>]) => (
                <TableRow key={groupId}>
                  <TableCell>
                    <Link
                      to={`/groups/${groups[0].active_group?.name}`}
                      sx={{
                        textDecoration: 'none',
                        color: 'inherit',
                      }}
                      component={RouterLink}>
                      {groups[0].active_group?.name}
                    </Link>
                  </TableCell>
                  <TableCell>{displayGroupType(groups[0].active_group)}</TableCell>
                  <TableCell>
                    <Ending memberships={groups} />
                  </TableCell>
                  <TableCell>
                    <Stack direction="row" spacing={1}>
                      {groups.map((group) =>
                        group.active_role_group_mapping == null ? (
                          <Chip
                            key="direct"
                            label="Direct"
                            color="primary"
                            onDelete={
                              group.active_group?.is_managed &&
                              (currentUser.id === user.id || canManageGroup(currentUser, group.active_group))
                                ? () => {
                                    currentUser.id == user.id
                                      ? onClickRemoveDirectAccess(
                                          user.id,
                                          group.active_group ?? ({} as PolymorphicGroup),
                                          true,
                                        )
                                      : removeUserFromGroup(group.active_group?.id ?? '');
                                  }
                                : undefined
                            }
                          />
                        ) : (
                          <Chip
                            key={group.active_role_group_mapping?.active_role_group?.name}
                            label={group.active_role_group_mapping?.active_role_group?.name}
                            variant="outlined"
                            color="primary"
                            onClick={() =>
                              navigate(`/roles/${group.active_role_group_mapping?.active_role_group?.name}`)
                            }
                            onDelete={
                              canManageGroup(currentUser, group.active_group) ||
                              isGroupOwner(currentUser, group.active_role_group_mapping.active_role_group?.id ?? '')
                                ? () =>
                                    onClickRemoveGroupFromRole(
                                      group.active_group ?? ({} as PolymorphicGroup),
                                      group.active_role_group_mapping?.active_role_group ?? ({} as RoleGroup),
                                      true,
                                    )
                                : undefined
                            }
                          />
                        ),
                      )}
                    </Stack>
                  </TableCell>
                </TableRow>
              ))
          ) : (
            <TableRow>
              <TableCell>
                <Typography variant="body2" color="text.secondary">
                  None
                </Typography>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
        <TableFooter>
          <TableRow></TableRow>
        </TableFooter>
      </Table>
    </TableContainer>
  );
}

interface MemberTableProps {
  user: OktaUser;
  memberships: Record<string, OktaUserGroupMember[]>;
  onClickRemoveGroupFromRole: (removeGroup: PolymorphicGroup, fromRole: RoleGroup, owner: boolean) => void;
  onClickRemoveDirectAccess: (id: string, fromGroup: PolymorphicGroup, owner: boolean) => void;
}

function MemberTable({user, memberships, onClickRemoveGroupFromRole, onClickRemoveDirectAccess}: MemberTableProps) {
  const currentUser = useCurrentUser();
  const navigate = useNavigate();

  const putGroupUsers = usePutGroupMembersById({
    onSuccess: () => navigate(0),
  });

  const removeUserFromGroup = React.useCallback(
    (groupId: string) => {
      putGroupUsers.mutate({
        body: {members_to_remove: [user.id], members_to_add: [], owners_to_remove: [], owners_to_add: []},
        pathParams: {groupId},
      });
    },
    [putGroupUsers],
  );

  return (
    <TableContainer component={Paper}>
      <Table sx={{minWidth: 650}} size="small" aria-label="member of groups">
        <TableHead>
          <TableRow>
            <TableCell colSpan={3}>
              <Typography variant="h6" color="text.accent">
                Member of Groups or Roles
              </Typography>
            </TableCell>
            <TableCell>
              <Box
                sx={{
                  display: 'flex',
                  justifyContent: 'flex-end',
                  alignItems: 'right',
                }}>
                <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                Total Groups: {Object.keys(memberships).length}
              </Box>
            </TableCell>
          </TableRow>
          <TableRow>
            <TableCell>Name</TableCell>
            <TableCell>Type</TableCell>
            <TableCell>Ending</TableCell>
            <TableCell>Direct or via Roles</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {Object.keys(memberships).length > 0 ? (
            Object.entries(memberships)
              .sort(sortUserGroups)
              .map(([groupId, groups]: [string, Array<OktaUserGroupMember>]) => (
                <TableRow key={groupId}>
                  <TableCell>
                    <Link
                      to={`/groups/${groups[0].active_group?.name}`}
                      sx={{
                        textDecoration: 'none',
                        color: 'inherit',
                      }}
                      component={RouterLink}>
                      {groups[0].active_group?.name}
                    </Link>
                  </TableCell>
                  <TableCell>{displayGroupType(groups[0].active_group)}</TableCell>
                  <TableCell>
                    <Ending memberships={groups} />
                  </TableCell>
                  <TableCell>
                    <Stack direction="row" spacing={1}>
                      {groups.map((group) =>
                        group.active_role_group_mapping == null ? (
                          <Chip
                            key="direct"
                            label="Direct"
                            color="primary"
                            onDelete={
                              group.active_group?.is_managed &&
                              (currentUser.id === user.id || canManageGroup(currentUser, group.active_group))
                                ? () => {
                                    currentUser.id == user.id
                                      ? onClickRemoveDirectAccess(
                                          user.id,
                                          group.active_group ?? ({} as PolymorphicGroup),
                                          false,
                                        )
                                      : removeUserFromGroup(group.active_group?.id ?? '');
                                  }
                                : undefined
                            }
                          />
                        ) : (
                          <Chip
                            key={group.active_role_group_mapping?.active_role_group?.name}
                            label={group.active_role_group_mapping?.active_role_group?.name}
                            variant="outlined"
                            color="primary"
                            onClick={() =>
                              navigate(`/roles/${group.active_role_group_mapping?.active_role_group?.name}`)
                            }
                            onDelete={
                              canManageGroup(currentUser, group.active_group) ||
                              isGroupOwner(currentUser, group.active_role_group_mapping.active_role_group?.id ?? '')
                                ? () =>
                                    onClickRemoveGroupFromRole(
                                      group.active_group ?? ({} as PolymorphicGroup),
                                      group.active_role_group_mapping?.active_role_group ?? ({} as RoleGroup),
                                      false,
                                    )
                                : undefined
                            }
                          />
                        ),
                      )}
                    </Stack>
                  </TableCell>
                </TableRow>
              ))
          ) : (
            <TableRow>
              <TableCell>
                <Typography variant="body2" color="text.secondary">
                  None
                </Typography>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
        <TableFooter>
          <TableRow></TableRow>
        </TableFooter>
      </Table>
    </TableContainer>
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

  const ownerships = groupBy(user.active_group_ownerships, (m) => m.active_user?.id);
  const memberships = groupBy(user.active_group_memberships, (m) => m.active_group?.id);

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
      <Container maxWidth="xl" sx={{mt: 4, mb: 4}}>
        <Grid container spacing={3}>
          <Grid item xs={12} md={5} lg={3}>
            <Paper
              sx={{
                p: 2,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                height: 240,
              }}>
              <UserAvatar name={displayUserName(user)} size={220} variant={'h1'} />
            </Paper>
          </Grid>
          <Grid item xs={12} md={7} lg={9} alignItems="center">
            <Paper
              sx={{
                p: 2,
                height: 240,
                alignText: 'center',
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'center',
              }}>
              <Grid container>
                <Grid
                  item
                  xs={12}
                  sx={{
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    position: 'relative',
                  }}>
                  <Typography variant="h3">
                    {user.deleted_at != null ? (
                      <>
                        <s>{displayUserName(user)}</s> is Deleted
                      </>
                    ) : (
                      displayUserName(user)
                    )}
                  </Typography>
                  <Typography variant="h5">{user.email?.toLowerCase()}</Typography>
                  <Stack style={{position: 'absolute', right: '2px'}}>
                    <Tooltip title="Audit" placement="right" PopperProps={moveTooltip}>
                      <IconButton aria-label="audit" to={`/users/${id}/audit`} component={RouterLink}>
                        <AuditIcon />
                      </IconButton>
                    </Tooltip>
                  </Stack>
                </Grid>
              </Grid>
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
            <Grid item xs={12}>
              <OwnerTable
                user={user}
                ownerships={ownerships}
                onClickRemoveGroupFromRole={showRemoveGroupFromRoleDialog}
                onClickRemoveDirectAccess={removeOwnDirectAccess}
              />
            </Grid>
            <Grid item xs={12}>
              <MemberTable
                user={user}
                memberships={memberships}
                onClickRemoveGroupFromRole={showRemoveGroupFromRoleDialog}
                onClickRemoveDirectAccess={removeOwnDirectAccess}
              />
            </Grid>
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
