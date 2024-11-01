import {Grid, Paper, Typography, Box, Chip, Stack, Tooltip, Divider} from '@mui/material';
import {grey} from '@mui/material/colors';
import CreateUpdateApp from '../CreateUpdate';
import DeleteApp from '../Delete';
import {App, OktaUser} from '../../../api/apiSchemas';
import {useNavigate} from 'react-router-dom';

import TagIcon from '@mui/icons-material/LocalOffer';
import {isAccessAdmin, isAppOwnerGroupOwner} from '../../../authorization';
interface AppsHeaderProps {
  app: App;
  currentUser: OktaUser;
}

export const AppsHeader: React.FC<AppsHeaderProps> = ({app, currentUser}) => {
  const navigate = useNavigate();
  const moveTooltip = {modifiers: [{name: 'offset', options: {offset: [0, -10]}}]};
  const hasActions = isAccessAdmin(currentUser) || isAppOwnerGroupOwner(currentUser, app.id ?? '');

  return (
    <Grid item xs={12}>
      <Paper sx={{p: 2}}>
        <Stack direction="column" gap={2}>
          <Stack alignItems="center" direction="column" gap={1} sx={{wordBreak: 'break-word'}}>
            <Typography variant="h3" sx={{margin: '5px 40px 0px 10px'}}>
              {app.name}
            </Typography>
            <Typography variant="h5" sx={{margin: '5px 40px 0px 10px'}}>
              {app.description}
            </Typography>
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
                      bgcolor: (theme) => (tagMap.active_tag!.enabled ? 'primary' : theme.palette.action.disabled),
                    }}
                  />
                ))}
              </Box>
            ) : null}
            {hasActions && (
              <>
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
              </>
            )}
          </Stack>
        </Stack>
      </Paper>
    </Grid>
  );
};

export default AppsHeader;
