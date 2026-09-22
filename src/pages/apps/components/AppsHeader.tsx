import {Grid, Paper, Typography, Box, Chip, Stack, Tooltip, Divider} from '@mui/material';
import ActionTooltip from '../../../components/ActionTooltip';
import CreateUpdateApp from '../CreateUpdate';
import DeleteApp from '../Delete';
import {AppDetail, OktaUserDetail} from '../../../api/apiSchemas';
import {useNavigate} from 'react-router-dom';
import React from 'react';

import TagIcon from '@mui/icons-material/LocalOffer';
import {isAccessAdmin, isAppOwnerGroupOwner} from '../../../authorization';
import MarkdownDescription from '../../../components/MarkdownDescription';

interface AppsHeaderProps {
  app: AppDetail;
  currentUser: OktaUserDetail;
}

export const AppsHeader: React.FC<AppsHeaderProps> = React.memo(({app, currentUser}) => {
  const navigate = useNavigate();
  const hasActions = React.useMemo(() => {
    return isAccessAdmin(currentUser) || isAppOwnerGroupOwner(currentUser, app.id ?? '');
  }, [currentUser, app.id]);

  const tagChips = React.useMemo(() => {
    if (!app.active_app_tags) return null;

    return app.active_app_tags.map((tagMap) => (
      <Chip
        key={'tag' + tagMap.active_tag!.id}
        label={tagMap.active_tag!.name}
        color="primary"
        onClick={() => navigate(`/tags/${tagMap.active_tag!.name}`)}
        icon={<TagIcon />}
        sx={{
          margin: '.125rem',
          marginTop: '.3125rem',
          bgcolor: (theme) => (tagMap.active_tag!.enabled ? 'primary' : theme.palette.action.disabled),
        }}
      />
    ));
  }, [app.active_app_tags, navigate]);

  return (
    <Grid item xs={12}>
      <Paper sx={{p: 2}}>
        <Stack direction="column" gap={2}>
          <Stack alignItems="center" direction="column" gap={1} sx={{wordBreak: 'break-word'}}>
            <Typography variant="h3" textAlign={'center'}>
              {app.name}
            </Typography>
            <MarkdownDescription description={app.description} />
            {tagChips && <Box>{tagChips}</Box>}
          </Stack>
          {hasActions && (
            <>
              <Divider />
              <Stack direction="row" justifyContent="center">
                <ActionTooltip title="Edit">
                  <CreateUpdateApp currentUser={currentUser} app={app} />
                </ActionTooltip>
                <ActionTooltip title="Delete">
                  <DeleteApp currentUser={currentUser} app={app} />
                </ActionTooltip>
              </Stack>
            </>
          )}
        </Stack>
      </Paper>
    </Grid>
  );
});

export default AppsHeader;
