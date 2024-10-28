import {Grid, Paper} from '@mui/material';
import CreateUpdateGroup from '../../groups/CreateUpdate';
import {OktaUser, App} from '../../../api/apiSchemas';

interface AppsAdminActionGroupProps {
  currentUser: OktaUser;
  app: App;
}

export const AppsAdminActionGroup: React.FC<AppsAdminActionGroupProps> = ({currentUser, app}) => {
  return (
    <Grid item xs={12} className={'app-detail app-detail-admin-action-group'}>
      <Paper
        sx={{
          p: 2,
          display: 'flex',
          alignItems: 'center',
        }}>
        <CreateUpdateGroup defaultGroupType={'app_group'} currentUser={currentUser} app={app}></CreateUpdateGroup>
      </Paper>
    </Grid>
  );
};
