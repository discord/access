import React, {useEffect, useState} from 'react';
import {Link as RouterLink, useNavigate, useParams} from 'react-router-dom';

import Box from '@mui/material/Box';
import Chip from '@mui/material/Chip';
import Container from '@mui/material/Container';
import Divider from '@mui/material/Divider';
import Grid from '@mui/material/Grid';
import Link from '@mui/material/Link';
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

import TagIcon from '@mui/icons-material/LocalOffer';

import Ending from '../../components/Ending';
import {groupBy, displayUserName} from '../../helpers';
import {isAccessAdmin, isAppOwnerGroupOwner} from '../../authorization';
import {useGetAppById} from '../../api/apiComponents';
import {App, OktaUserGroupMember} from '../../api/apiSchemas';

import {useCurrentUser} from '../../authentication';
import CreateUpdateGroup from '../groups/CreateUpdate';
import CreateUpdateApp from './CreateUpdate';
import DeleteApp from './Delete';
import NotFound from '../NotFound';
import Loading from '../../components/Loading';

function sortGroupMembers(
  [aUserId, aUsers]: [string, Array<OktaUserGroupMember>],
  [bUserId, bUsers]: [string, Array<OktaUserGroupMember>],
): number {
  let aEmail = aUsers[0].active_user?.email ?? '';
  let bEmail = bUsers[0].active_user?.email ?? '';
  return aEmail.localeCompare(bEmail);
}

function groupMemberships(
  memberships: Array<OktaUserGroupMember> | undefined,
): Map<string, Array<OktaUserGroupMember>> {
  return groupBy(memberships ?? [], 'active_user.id');
}

export default function ReadApp() {
  const currentUser = useCurrentUser();

  const {id} = useParams();
  const navigate = useNavigate();

  const {data, isError, isLoading} = useGetAppById({
    pathParams: {appId: id ?? ''},
  });

  if (isError) {
    return <NotFound />;
  }

  if (isLoading) {
    return <Loading />;
  }

  const app = data ?? ({} as App);

  const moveTooltip = {modifiers: [{name: 'offset', options: {offset: [0, -10]}}]};

  return (
    <React.Fragment>
      <Container maxWidth="lg" sx={{mt: 4, mb: 4}}>
        <Grid container spacing={3}>
          <Grid item xs={12}>
            <Paper sx={{py: 4, px: 2}}>
              <Stack direction="column" gap={2}>
                <Stack alignItems="center" direction="column" gap={1}>
                  <Typography variant="h3">{app.name}</Typography>
                  <Typography variant="h5">{app.description}</Typography>
                  {app.active_app_tags ? (
                    <Box>
                      {app.active_app_tags.map((tagMap) => (
                        <Chip
                          key={'tag' + tagMap.active_tag!.id}
                          label={tagMap.active_tag!.name}
                          color="primary"
                          onClick={() => navigate(`/tags/${tagMap.active_tag!.name}`)}
                          icon={<TagIcon />}
                          sx={{
                            margin: '2px',
                            marginTop: '5px',
                            bgcolor: (theme) =>
                              tagMap.active_tag!.enabled ? 'primary' : theme.palette.action.disabled,
                          }}
                        />
                      ))}
                    </Box>
                  ) : null}
                </Stack>
                <Divider />
                <Stack direction="row" justifyContent="center">
                  <Tooltip title="Edit" placement="top" PopperProps={moveTooltip}>
                    <div>
                      <CreateUpdateApp currentUser={currentUser} app={app} />
                    </div>
                  </Tooltip>
                  <Tooltip title="Delete" placement="top" PopperProps={moveTooltip}>
                    <div>
                      <DeleteApp currentUser={currentUser} app={app} />
                    </div>
                  </Tooltip>
                </Stack>
              </Stack>
            </Paper>
          </Grid>
          {isAccessAdmin(currentUser) || isAppOwnerGroupOwner(currentUser, app.id ?? '') ? (
            <Grid item xs={12}>
              <Paper
                sx={{
                  p: 2,
                  display: 'flex',
                  alignItems: 'center',
                }}>
                <CreateUpdateGroup
                  defaultGroupType={'app_group'}
                  currentUser={currentUser}
                  app={app}></CreateUpdateGroup>
              </Paper>
            </Grid>
          ) : null}
          {app.active_owner_app_groups?.map((appGroup) => (
            <React.Fragment key={appGroup.id}>
              <Grid item xs={6} key={appGroup.id + 'owners'}>
                <TableContainer component={Paper}>
                  <Table sx={{minWidth: 325}} size="small" aria-label="app owners">
                    <TableHead>
                      <TableRow>
                        <TableCell colSpan={2}>
                          <Stack direction="column" spacing={1}>
                            <Typography variant="h6" color="text.accent">
                              <Link
                                to={`/groups/${appGroup.name}`}
                                sx={{
                                  textDecoration: 'none',
                                  color: 'inherit',
                                }}
                                component={RouterLink}>
                                App Owners
                              </Link>
                            </Typography>
                            <Typography variant="body1" color="text.secondary">
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
              </Grid>
              <Grid item xs={6} key={appGroup.id + 'members'}>
                <TableContainer component={Paper}>
                  <Table sx={{minWidth: 325}} size="small" aria-label="app owner members">
                    <TableHead>
                      <TableRow>
                        <TableCell colSpan={2}>
                          <Stack direction="column" spacing={1}>
                            <Typography variant="h6" color="text.accent">
                              <Link
                                to={`/groups/${appGroup.name}`}
                                sx={{
                                  textDecoration: 'none',
                                  color: 'inherit',
                                }}
                                component={RouterLink}>
                                App Owners Group Members
                              </Link>
                            </Typography>
                            <Typography variant="body1" color="text.secondary">
                              Members of Owners Okta Group
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
                            Total Members: {Object.keys(groupMemberships(appGroup.active_user_memberships)).length}
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
                      {Object.keys(groupMemberships(appGroup.active_user_memberships)).length > 0 ? (
                        Object.entries(groupMemberships(appGroup.active_user_memberships))
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
              </Grid>
            </React.Fragment>
          ))}
          {app.active_non_owner_app_groups?.map((appGroup) => (
            <React.Fragment key={appGroup.id}>
              <Grid item xs={6} key={appGroup.id + 'owners'}>
                <TableContainer component={Paper}>
                  <Table sx={{minWidth: 325}} size="small" aria-label="app group owners">
                    <TableHead>
                      <TableRow>
                        <TableCell colSpan={2}>
                          <Stack direction="column">
                            <Typography variant="h6" color="text.accent">
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
                            <Typography variant="body1" color="text.secondary">
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
                          ))
                      ) : (
                        <TableRow>
                          <TableCell>
                            <Typography variant="body2" color="text.secondary">
                              All app owners are implicitly app group owners
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
              </Grid>
              <Grid item xs={6} key={appGroup.id + 'members'}>
                <TableContainer component={Paper}>
                  <Table sx={{minWidth: 325}} size="small" aria-label="app group members">
                    <TableHead>
                      <TableRow>
                        <TableCell colSpan={2}>
                          <Stack direction="column">
                            <Typography variant="h6" color="text.accent">
                              <Link
                                to={`/groups/${appGroup.name}`}
                                sx={{
                                  textDecoration: 'none',
                                  color: 'inherit',
                                }}
                                component={RouterLink}>
                                {appGroup.name} Group Members
                              </Link>
                            </Typography>
                            <Typography variant="body1" color="text.secondary">
                              Members of App Okta Group
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
                            Total Members: {Object.keys(groupMemberships(appGroup.active_user_memberships)).length}
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
                      {Object.keys(groupMemberships(appGroup.active_user_memberships)).length > 0 ? (
                        Object.entries(groupMemberships(appGroup.active_user_memberships))
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
              </Grid>
            </React.Fragment>
          ))}
        </Grid>
      </Container>
    </React.Fragment>
  );
}
