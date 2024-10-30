import {Grid, Paper, Typography, Box, Chip, Stack, Tooltip} from '@mui/material';
import {grey} from '@mui/material/colors';
import CreateUpdateApp from '../CreateUpdate';
import DeleteApp from '../Delete';
import {App, OktaUser} from '../../../api/apiSchemas';
import {useNavigate} from 'react-router-dom';

import TagIcon from '@mui/icons-material/LocalOffer';
import {MoveTooltip} from '.';

interface AppsHeaderProps {
  app: App;
  moveTooltip: MoveTooltip;
  currentUser: OktaUser;
}

const AppsHeader: React.FC<AppsHeaderProps> = ({app, moveTooltip, currentUser}) => {
  const navigate = useNavigate();

  const classNames = `app-detail-header app-detail-header ${app.name}`;

  return (
    <Grid item xs={12} className={classNames}>
      <Paper
        sx={{
          p: 2,
          height: '15rem',
          minWidth: '20rem',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          textAlign: 'center',
          position: 'relative',
        }}>
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
                  bgcolor: tagMap.active_tag!.enabled ? 'primary' : grey[500],
                }}
              />
            ))}
          </Box>
        ) : null}
        <Stack style={{position: 'absolute', right: '10px'}}>
          <Tooltip title="Edit" placement="right" PopperProps={moveTooltip}>
            <div>
              <CreateUpdateApp currentUser={currentUser} app={app} />
            </div>
          </Tooltip>
          <Tooltip title="Delete" placement="right" PopperProps={moveTooltip}>
            <div>
              <DeleteApp currentUser={currentUser} app={app} />
            </div>
          </Tooltip>
        </Stack>
      </Paper>
    </Grid>
  );
};

export default AppsHeader;