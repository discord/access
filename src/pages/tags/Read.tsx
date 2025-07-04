import React from 'react';

import {Link as RouterLink, useNavigate, useParams} from 'react-router-dom';

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
import TableFooter from '@mui/material/TableFooter';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';

import DeleteIcon from '@mui/icons-material/Close';
import Disabled from '@mui/icons-material/PauseCircle';
import Enabled from '@mui/icons-material/TaskAlt';

import {useGetTagById, usePutGroupById, usePutAppById} from '../../api/apiComponents';
import {App, AppGroup, PolymorphicGroup, Tag} from '../../api/apiSchemas';

import {useCurrentUser} from '../../authentication';
import {isAccessAdmin} from '../../authorization';
import ChangeTitle from '../../tab-title';
import AddApps from './AddApps';
import AddGroups from './AddGroups';
import CreateUpdateTag from './CreateUpdate';
import NotFound from '../NotFound';
import Loading from '../../components/Loading';
import DeleteTag from './Delete';
import {EmptyListEntry} from '../../components/EmptyListEntry';

export default function ReadTag() {
  const currentUser = useCurrentUser();

  const {id} = useParams();
  const navigate = useNavigate();

  const {data, isError, isLoading} = useGetTagById({
    pathParams: {tagId: id ?? ''},
  });

  const updateApp = usePutAppById({
    onSuccess: () => navigate(0),
  });

  const updateGroup = usePutGroupById({
    onSuccess: () => navigate(0),
  });

  if (isError) {
    return <NotFound />;
  }

  if (isLoading) {
    return <Loading />;
  }

  const tag = data ?? ({} as Tag);

  const removeTagFromApp = (appToRemove: App, tagId: string) => {
    let app: App = {
      name: appToRemove.name,
      description: appToRemove.description,
      tags_to_remove: [tagId],
    };

    updateApp.mutate({
      body: app,
      pathParams: {appId: appToRemove?.id ?? ''},
    });
  };

  const removeTagFromGroup = (groupToRemove: PolymorphicGroup, tagId: string) => {
    let group: PolymorphicGroup = {
      name: groupToRemove.name,
      description: groupToRemove.description,
      type: groupToRemove.type,
      tags_to_remove: [tagId],
    };

    switch (groupToRemove.type) {
      case 'okta_group':
        break;
      case 'role_group':
        break;
      case 'app_group':
        const appGroup = group as AppGroup;
        appGroup.app_id = (groupToRemove as AppGroup).app?.id ?? '';
        break;
    }

    updateGroup.mutate({
      body: group,
      pathParams: {groupId: groupToRemove?.id ?? ''},
    });
  };

  const moveTooltip = {modifiers: [{name: 'offset', options: {offset: [0, -10]}}]};

  const constraintsNames: Record<string, string> = {
    member_time_limit: 'Member Time Limit',
    owner_time_limit: 'Owner Time Limit',
    require_owner_reason: 'Required to Provide Ownership Reason?',
    require_member_reason: 'Required to Provide Membership Reason?',
    disallow_self_add_ownership: 'Owners may not add selves as owners?',
    disallow_self_add_membership: 'Owners may not add selves as members?',
  };

  const hasActions = tag != null && tag.deleted_at == null && isAccessAdmin(currentUser);
  return (
    <React.Fragment>
      <ChangeTitle title={`${tag.name} Tag`} />
      <Container maxWidth="lg" sx={{my: 4}}>
        <Grid container spacing={3}>
          <Grid item xs={12}>
            <Paper sx={{p: 2}}>
              <Stack direction="column" gap={2}>
                <Stack alignItems="center" direction="column" sx={{wordBreak: 'break-word'}} gap={1}>
                  <Typography variant="h3" textAlign="center">
                    {tag.name}
                  </Typography>
                  <Typography variant="h5" textAlign="center">
                    {tag.description}
                  </Typography>
                  {tag.enabled ? (
                    <Box>
                      <Chip color="primary" icon={<Enabled />} label="Enabled" sx={{marginTop: '10px'}} />
                    </Box>
                  ) : (
                    <Box>
                      <Chip
                        icon={<Disabled />}
                        label="Disabled"
                        sx={{
                          marginTop: '10px',
                          bgcolor: (theme) => theme.palette.action.disabledBackground,
                        }}
                      />
                    </Box>
                  )}
                </Stack>
                {hasActions && (
                  <>
                    <Divider />
                    <Stack direction="row" justifyContent="center">
                      <Tooltip title="Edit" placement="top" PopperProps={moveTooltip}>
                        <div>
                          <CreateUpdateTag currentUser={currentUser} tag={tag} />
                        </div>
                      </Tooltip>
                      <Tooltip title="Delete" placement="top" PopperProps={moveTooltip}>
                        <div>
                          <DeleteTag currentUser={currentUser} tag={tag} />
                        </div>
                      </Tooltip>
                    </Stack>
                  </>
                )}
              </Stack>
            </Paper>
          </Grid>
          <Grid item xs={12}>
            <TableContainer component={Paper}>
              <Table sx={{minWidth: 650}} size="small" aria-label="apps with tag">
                <TableHead>
                  <TableRow>
                    <TableCell colSpan={3}>
                      <Stack direction="row" spacing={1} sx={{display: 'flex', alignItems: 'center'}}>
                        <Typography variant="h6" color="text.accent">
                          Tag Constraints
                        </Typography>
                      </Stack>
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell>Name</TableCell>
                    <TableCell>Value</TableCell>
                    <TableCell colSpan={2}>
                      <Grid container>
                        <Grid item xs={12}>
                          <Box
                            sx={{
                              display: 'flex',
                              justifyContent: 'flex-end',
                              alignItems: 'right',
                            }}>
                            <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                            Total: {tag.constraints ? Object.keys(tag.constraints).length : 0}
                          </Box>
                        </Grid>
                      </Grid>
                    </TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {tag.constraints && Object.keys(tag.constraints).length > 0 ? (
                    Object.keys(tag.constraints).map((key: string) => (
                      <TableRow key={'constraint' + key}>
                        <TableCell>{constraintsNames[key]}</TableCell>
                        <TableCell colSpan={2}>
                          {
                            key == 'member_time_limit' || key == 'owner_time_limit'
                              ? tag.constraints![key] / 86400 + ' days' // Display days not seconds
                              : tag.constraints![key]
                                ? 'Yes'
                                : 'No' // Display Yes and No not booleans
                          }
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
          </Grid>
          <Grid item xs={12}>
            <TableContainer component={Paper}>
              <Table sx={{minWidth: 650}} size="small" aria-label="apps with tag">
                <TableHead>
                  <TableRow>
                    <TableCell colSpan={2}>
                      <Stack direction="row" spacing={1} sx={{display: 'flex', alignItems: 'center'}}>
                        <Typography variant="h6" color="text.accent">
                          Apps with Tag
                        </Typography>
                      </Stack>
                    </TableCell>
                    <TableCell align="right">
                      <Stack direction="row" spacing={1} justifyContent={'flex-end'}>
                        <AddApps currentUser={currentUser} tag={tag} />
                      </Stack>
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell>Name</TableCell>
                    <TableCell colSpan={2}>
                      <Grid container>
                        <Grid item xs={12}>
                          <Box
                            sx={{
                              display: 'flex',
                              justifyContent: 'flex-end',
                              alignItems: 'right',
                            }}>
                            <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                            Total: {tag.active_app_tags ? tag.active_app_tags.length : 0}
                          </Box>
                        </Grid>
                      </Grid>
                    </TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {tag.active_app_tags && tag.active_app_tags.length > 0 ? (
                    tag.active_app_tags.map((tagMap) => (
                      <TableRow key={'taggedapps' + tagMap.active_app?.id}>
                        <TableCell colSpan={2}>
                          <Link
                            to={`/apps/${tagMap.active_app?.name}`}
                            sx={{
                              textDecoration: 'none',
                              color: 'inherit',
                            }}
                            component={RouterLink}>
                            {tagMap.active_app?.name}
                          </Link>
                        </TableCell>
                        <TableCell align="right">
                          {isAccessAdmin(currentUser) ? (
                            <IconButton size="small" onClick={() => removeTagFromApp(tagMap.active_app!, tag.id)}>
                              <DeleteIcon />
                            </IconButton>
                          ) : null}
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
          </Grid>
          <Grid item xs={12}>
            <TableContainer component={Paper}>
              <Table sx={{minWidth: 650}} size="small" aria-label="group owners">
                <TableHead>
                  <TableRow>
                    <TableCell colSpan={3}>
                      <Stack direction="row" spacing={1} sx={{display: 'flex', alignItems: 'center'}}>
                        <Typography variant="h6" color="text.accent">
                          Groups with Tag
                        </Typography>
                      </Stack>
                    </TableCell>
                    <TableCell align="right">
                      <Stack direction="row" spacing={1} justifyContent={'flex-end'}>
                        <AddGroups currentUser={currentUser} tag={tag} />
                      </Stack>
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell>Name</TableCell>
                    <TableCell>Group Type</TableCell>
                    <TableCell colSpan={2}>
                      <Grid container>
                        <Grid item xs={3}>
                          Direct or via App
                        </Grid>
                        <Grid item xs={9}>
                          <Box
                            sx={{
                              display: 'flex',
                              justifyContent: 'flex-end',
                              alignItems: 'right',
                            }}>
                            <Divider sx={{mx: 2}} orientation="vertical" flexItem />
                            Total: {tag.active_group_tags ? tag.active_group_tags.length : 0}
                          </Box>
                        </Grid>
                      </Grid>
                    </TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {tag.active_group_tags && tag.active_group_tags.length > 0 ? (
                    tag.active_group_tags.map((tagMap) => (
                      <TableRow
                        key={
                          'taggedgroups' + tagMap.active_group?.id + (tagMap.active_app_tag_mapping ? 'app' : 'direct')
                        }>
                        <TableCell>
                          <Link
                            to={`/groups/${tagMap.active_group?.name}`}
                            sx={{
                              textDecoration: 'none',
                              color: 'inherit',
                            }}
                            component={RouterLink}>
                            {tagMap.active_group?.name}
                          </Link>
                        </TableCell>
                        <TableCell>
                          {tagMap.active_group?.type == 'role_group'
                            ? 'Role Group'
                            : tagMap.active_group?.type == 'app_group'
                              ? 'App Group'
                              : 'Group'}
                        </TableCell>
                        <TableCell>
                          {tagMap.active_app_tag_mapping ? (
                            <Chip
                              key={'group' + tagMap.active_group?.id}
                              label={(tagMap.active_group as AppGroup).app?.name}
                              color="primary"
                              variant="outlined"
                            />
                          ) : (
                            <Chip key={'group' + tagMap.active_group?.id} label="Direct" color="primary" />
                          )}
                        </TableCell>
                        <TableCell align="right">
                          {isAccessAdmin(currentUser) && !tagMap.active_app_tag_mapping ? (
                            <IconButton size="small" onClick={() => removeTagFromGroup(tagMap.active_group!, tag.id)}>
                              <DeleteIcon />
                            </IconButton>
                          ) : null}
                        </TableCell>
                      </TableRow>
                    ))
                  ) : (
                    <EmptyListEntry cellProps={{colSpan: 4}} />
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
    </React.Fragment>
  );
}
