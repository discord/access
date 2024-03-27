import * as React from 'react';
import {useNavigate} from 'react-router-dom';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import AppIcon from '@mui/icons-material/AppShortcut';
import Alert from '@mui/material/Alert';
import FormControl from '@mui/material/FormControl';
import Grid from '@mui/material/Grid';
import Typography from '@mui/material/Typography';
import Box from '@mui/material/Box';
import Divider from '@mui/material/Divider';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemText from '@mui/material/ListItemText';
import IconButton from '@mui/material/IconButton';
import InputLabel from '@mui/material/InputLabel';
import DeleteIcon from '@mui/icons-material/Close';
import CircularProgress from '@mui/material/CircularProgress';

import {FormContainer, SelectElement, AutocompleteElement} from 'react-hook-form-mui';

import {useGetApps, usePutAppById, PutAppByIdError, PutAppByIdVariables} from '../../api/apiComponents';
import {App, OktaUser, Tag} from '../../api/apiSchemas';
import {isAccessAdmin} from '../../authorization';

interface AddAppsButtonProps {
  setOpen(open: boolean): any;
}

function AddAppsButton(props: AddAppsButtonProps) {
  return (
    <Button variant="contained" onClick={() => props.setOpen(true)} endIcon={<AppIcon />}>
      Add Apps
    </Button>
  );
}

interface AddAppsDialogProps {
  currentUser: OktaUser;
  tag: Tag;
  setOpen(open: boolean): any;
}

function AddAppsDialog(props: AddAppsDialogProps) {
  const navigate = useNavigate();

  const [numUpdates, setNumUpdates] = React.useState(0);
  const [appUpdatesCompleted, setAppUpdatesCompleted] = React.useState(0);
  const [appUpdatesErrored, setAppUpdatesErrored] = React.useState(0);

  const [appSearchInput, setAppSearchInput] = React.useState('');
  const [apps, setApps] = React.useState<Array<App>>([]);
  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);

  const {data: appSearchData} = useGetApps({
    queryParams: {
      page: 0,
      per_page: 10,
      q: appSearchInput,
    },
  });
  const appSearchOptions = appSearchData?.results ?? [];

  const complete = (
    completedApp: App | undefined,
    error: PutAppByIdError | null,
    variables: PutAppByIdVariables,
    context: any,
  ) => {
    if (error != null) {
      setRequestError(error.payload.toString());
      setAppUpdatesErrored((prevValue) => prevValue + 1);
    } else {
      setAppUpdatesCompleted((prevValue) => prevValue + 1);
    }
  };

  React.useEffect(() => {
    if (submitting) {
      if (numUpdates == appUpdatesCompleted + appUpdatesErrored) {
        setSubmitting(false);
        if (appUpdatesErrored > 0) {
          setNumUpdates(0);
          setAppUpdatesCompleted(0);
          setAppUpdatesErrored(0);
        } else {
          props.setOpen(false);
          navigate(0);
        }
      }
    }
  }, [appUpdatesCompleted, appUpdatesErrored]);

  const updateApp = usePutAppById({
    onSettled: complete,
  });

  const submit = () => {
    setSubmitting(true);
    setNumUpdates(apps.length);

    for (var i = 0; i < apps.length; i++) {
      let app: App = {
        name: apps[i].name,
        description: apps[i].description,
        tags_to_add: [props.tag.id],
      };

      updateApp.mutate({
        body: app,
        pathParams: {appId: apps[i]?.id ?? ''},
      });
    }
  };

  const removeAppFromList = (appId: string) => {
    setApps(apps.filter((app) => app.id != appId));
  };

  return (
    <Dialog open fullWidth onClose={() => props.setOpen(false)}>
      <FormContainer onSuccess={() => submit()}>
        <DialogTitle>Add Apps</DialogTitle>
        <DialogContent>
          {requestError != '' ? <Alert severity="error">{requestError}</Alert> : null}
          <FormControl margin="normal" fullWidth>
            <AutocompleteElement
              label={'Search for Apps to Add'}
              name="app"
              options={appSearchOptions}
              autocompleteProps={{
                getOptionLabel: (option) => option.name,
                isOptionEqualToValue: (option, value) => option.id == value.id,
                filterOptions: (options) => options.filter((option) => !apps.map((app) => app.id).includes(option.id)),
                onInputChange: (event, newInputValue, reason) => {
                  if (reason != 'reset') {
                    setAppSearchInput(newInputValue);
                  }
                },
                onChange: (event, value) => {
                  if (value != null) {
                    setApps([value, ...apps]);
                    setAppSearchInput('');
                  }
                },
                inputValue: appSearchInput,
                renderOption: (props, option, state) => {
                  return (
                    <li {...props}>
                      <Grid container alignItems="center">
                        <Grid item>
                          <Box>{option.name}</Box>
                          <Typography variant="body2" color="text.secondary">
                            App
                          </Typography>
                        </Grid>
                      </Grid>
                    </li>
                  );
                },
              }}
            />
          </FormControl>
          <FormControl margin="normal" fullWidth>
            <InputLabel shrink={apps.length > 0}>Apps to Add</InputLabel>
            <List
              sx={{
                overflow: 'auto',
                minHeight: 300,
                maxHeight: 600,
                backgroundColor: (theme) =>
                  theme.palette.mode === 'light' ? theme.palette.grey[100] : theme.palette.grey[900],
              }}
              dense={true}>
              {apps.map((app) => (
                <React.Fragment key={app.id}>
                  <ListItem
                    sx={{py: 0}}
                    secondaryAction={
                      <IconButton edge="end" aria-label="delete" onClick={() => removeAppFromList(app?.id ?? '')}>
                        <DeleteIcon />
                      </IconButton>
                    }>
                    <ListItemText primary={app?.name ?? ''} secondary="App" />
                  </ListItem>
                  <Divider />
                </React.Fragment>
              ))}
            </List>
          </FormControl>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => props.setOpen(false)}>Cancel</Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? <CircularProgress size={24} /> : 'Add'}
          </Button>
        </DialogActions>
      </FormContainer>
    </Dialog>
  );
}

interface AddAppsProps {
  currentUser: OktaUser;
  tag: Tag;
}

export default function AddApps(props: AddAppsProps) {
  const [open, setOpen] = React.useState(false);

  if (props.tag.deleted_at != null || !isAccessAdmin(props.currentUser)) {
    return null;
  }

  return (
    <>
      <AddAppsButton setOpen={setOpen} />
      {open ? <AddAppsDialog currentUser={props.currentUser} tag={props.tag} setOpen={setOpen} /> : null}
    </>
  );
}
