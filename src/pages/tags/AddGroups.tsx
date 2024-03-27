import * as React from 'react';
import {useNavigate} from 'react-router-dom';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import GroupAddIcon from '@mui/icons-material/GroupAdd';
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

import {FormContainer, AutocompleteElement} from 'react-hook-form-mui';

import {useGetGroups, usePutGroupById, PutGroupByIdError, PutGroupByIdVariables} from '../../api/apiComponents';
import {PolymorphicGroup, OktaUser, Tag, AppGroup} from '../../api/apiSchemas';
import {isAccessAdmin} from '../../authorization';

interface AddGroupsButtonProps {
  setOpen(open: boolean): any;
}

function AddGroupsButton(props: AddGroupsButtonProps) {
  return (
    <Button variant="contained" onClick={() => props.setOpen(true)} endIcon={<GroupAddIcon />}>
      {'Add Groups'}
    </Button>
  );
}

interface AddGroupsDialogProps {
  currentUser: OktaUser;
  tag: Tag;
  setOpen(open: boolean): any;
}

const GROUP_TYPE_ID_TO_LABELS: Record<string, string> = {
  okta_group: 'Group',
  app_group: 'App Group',
  role_group: 'Role',
} as const;

function AddGroupsDialog(props: AddGroupsDialogProps) {
  const navigate = useNavigate();

  const [numUpdates, setNumUpdates] = React.useState(0);
  const [groupUpdatesCompleted, setGroupUpdatesCompleted] = React.useState(0);
  const [groupUpdatesErrored, setGroupUpdatesErrored] = React.useState(0);

  const [groupSearchInput, setGroupSearchInput] = React.useState('');
  const [groups, setGroups] = React.useState<Array<PolymorphicGroup>>([]);
  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);

  const {data: groupSearchData} = useGetGroups({
    queryParams: {
      page: 0,
      per_page: 10,
      managed: true,
      q: groupSearchInput,
    },
  });
  const groupSearchOptions = groupSearchData?.results ?? [];

  const complete = (
    completedGroup: PolymorphicGroup | undefined,
    error: PutGroupByIdError | null,
    variables: PutGroupByIdVariables,
    context: any,
  ) => {
    if (error != null) {
      setRequestError(error.payload.toString());
      setGroupUpdatesErrored((prevValue) => prevValue + 1);
    } else {
      setGroupUpdatesCompleted((prevValue) => prevValue + 1);
    }
  };

  React.useEffect(() => {
    if (submitting) {
      if (numUpdates == groupUpdatesCompleted + groupUpdatesErrored) {
        setSubmitting(false);
        if (groupUpdatesErrored > 0) {
          setNumUpdates(0);
          setGroupUpdatesCompleted(0);
          setGroupUpdatesErrored(0);
        } else {
          props.setOpen(false);
          navigate(0);
        }
      }
    }
  }, [groupUpdatesCompleted, groupUpdatesErrored]);

  const updateGroup = usePutGroupById({
    onSettled: complete,
  });

  const submit = () => {
    setSubmitting(true);
    setNumUpdates(groups.length);

    for (var i = 0; i < groups.length; i++) {
      let group: PolymorphicGroup = {
        name: groups[i].name,
        description: groups[i].description,
        type: groups[i].type,
        tags_to_add: [props.tag.id],
      };

      if (groups[i].type == 'app_group') {
        (group as AppGroup).app_id = (groups[i] as AppGroup).app!.id!;
      }

      updateGroup.mutate({
        body: group,
        pathParams: {groupId: groups[i]?.id ?? ''},
      });
    }
  };

  const removeGroupFromList = (groupId: string) => {
    setGroups(groups.filter((group) => group.id != groupId));
  };

  return (
    <Dialog open fullWidth onClose={() => props.setOpen(false)}>
      <FormContainer onSuccess={() => submit()}>
        <DialogTitle>Add Groups</DialogTitle>
        <DialogContent>
          {requestError != '' ? <Alert severity="error">{requestError}</Alert> : null}
          <FormControl margin="normal" fullWidth>
            <AutocompleteElement
              label={'Search for Groups to Add'}
              name="group"
              options={groupSearchOptions}
              autocompleteProps={{
                getOptionLabel: (option) => option.name,
                isOptionEqualToValue: (option, value) => option.id == value.id,
                filterOptions: (options) =>
                  options.filter(
                    (option) => option.is_managed == true && !groups.map((group) => group.id).includes(option.id),
                  ),
                onInputChange: (event, newInputValue, reason) => {
                  if (reason != 'reset') {
                    setGroupSearchInput(newInputValue);
                  }
                },
                onChange: (event, value) => {
                  if (value != null) {
                    setGroups([value, ...groups]);
                    setGroupSearchInput('');
                  }
                },
                inputValue: groupSearchInput,
                renderOption: (props, option, state) => {
                  return (
                    <li {...props}>
                      <Grid container alignItems="center">
                        <Grid item>
                          <Box>{option.name}</Box>
                          <Typography variant="body2" color="text.secondary">
                            {GROUP_TYPE_ID_TO_LABELS[option.type]}
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
            <InputLabel shrink={groups.length > 0}>Groups to Add</InputLabel>
            <List
              sx={{
                overflow: 'auto',
                minHeight: 300,
                maxHeight: 600,
                backgroundColor: (theme) =>
                  theme.palette.mode === 'light' ? theme.palette.grey[100] : theme.palette.grey[900],
              }}
              dense={true}>
              {groups.map((group) => (
                <React.Fragment key={group.id}>
                  <ListItem
                    sx={{py: 0}}
                    secondaryAction={
                      <IconButton edge="end" aria-label="delete" onClick={() => removeGroupFromList(group?.id ?? '')}>
                        <DeleteIcon />
                      </IconButton>
                    }>
                    <ListItemText
                      primary={group?.name ?? ''}
                      secondary={GROUP_TYPE_ID_TO_LABELS[group?.type ?? 'okta_group']}
                    />
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

interface AddGroupsProps {
  currentUser: OktaUser;
  tag: Tag;
}

export default function AddGroups(props: AddGroupsProps) {
  const [open, setOpen] = React.useState(false);

  if (props.tag.deleted_at != null || !isAccessAdmin(props.currentUser)) {
    return null;
  }

  return (
    <>
      <AddGroupsButton setOpen={setOpen} />
      {open ? <AddGroupsDialog currentUser={props.currentUser} tag={props.tag} setOpen={setOpen} /> : null}
    </>
  );
}
