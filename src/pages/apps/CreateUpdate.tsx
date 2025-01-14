import * as React from 'react';
import {useNavigate} from 'react-router-dom';
import AppAddIcon from '@mui/icons-material/AddBox';
import Alert from '@mui/material/Alert';
import Autocomplete from '@mui/material/Autocomplete';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import CircularProgress from '@mui/material/CircularProgress';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import EditIcon from '@mui/icons-material/Edit';
import FormControl from '@mui/material/FormControl';
import IconButton from '@mui/material/IconButton';
import TextField from '@mui/material/TextField';

import {FormContainer, TextFieldElement} from 'react-hook-form-mui';

import {
  useCreateApp,
  useGetTags,
  usePutAppById,
  CreateAppError,
  PutAppByIdError,
  CreateAppVariables,
  PutAppByIdVariables,
} from '../../api/apiComponents';
import {App, AppTagMap, OktaUser, Tag} from '../../api/apiSchemas';
import {isAccessAdmin, isAppOwnerGroupOwner, ACCESS_APP_RESERVED_NAME} from '../../authorization';
import accessConfig from '../../config/accessConfig';

interface AppButtonProps {
  setOpen(open: boolean): any;
  app?: App;
}

function AppButton(props: AppButtonProps) {
  if (props.app == null) {
    return (
      <Button variant="contained" onClick={() => props.setOpen(true)} endIcon={<AppAddIcon />}>
        Create App
      </Button>
    );
  } else {
    return (
      <IconButton aria-label="edit" onClick={() => props.setOpen(true)}>
        <EditIcon />
      </IconButton>
    );
  }
}

interface AppDialogProps {
  currentUser: OktaUser;
  setOpen(open: boolean): any;
  app?: App;
  access_app?: boolean;
}

function AppDialog(props: AppDialogProps) {
  const navigate = useNavigate();

  const defaultTags =
    props.app && props.app.active_app_tags && props.app.active_app_tags.length > 0
      ? props.app.active_app_tags.map((tagMap: AppTagMap) => tagMap.active_tag!)
      : [];
  const [selectedTags, setSelectedTags] = React.useState<Array<Tag>>(defaultTags);
  const [tagSearchInput, setTagSearchInput] = React.useState('');
  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);

  const complete = (
    completedApp: App | undefined,
    error: CreateAppError | PutAppByIdError | null,
    variables: CreateAppVariables | PutAppByIdVariables,
    context: any,
  ) => {
    setSubmitting(false);
    if (error != null) {
      setRequestError(error.payload.toString());
    } else {
      props.setOpen(false);
      if ((props.app?.name ?? '') == (completedApp?.name ?? '')) {
        navigate(0);
      } else {
        navigate('/apps/' + encodeURIComponent(completedApp?.name ?? ''));
      }
    }
  };

  const createApp = useCreateApp({
    onSettled: complete,
  });
  const updateApp = usePutAppById({
    onSettled: complete,
  });

  const {data: tagSearchData} = useGetTags({
    queryParams: {
      page: 0,
      per_page: 10,
      q: tagSearchInput,
    },
  });
  const tagSearchOptions = tagSearchData?.results ?? [];

  const submit = (app: App) => {
    setSubmitting(true);

    if (props.app) {
      app.tags_to_add = selectedTags.filter((x) => !defaultTags.includes(x)).map((tag: Tag) => tag.id);
      app.tags_to_remove = defaultTags.filter((x) => !selectedTags.includes(x)).map((tag: Tag) => tag.id);
    } else {
      app.tags_to_add = selectedTags.map((tag: Tag) => tag.id);
    }

    if (props.app == null) {
      createApp.mutate({body: app});
    } else {
      updateApp.mutate({
        body: app,
        pathParams: {appId: props.app?.id ?? ''},
      });
    }
  };

  const createOrUpdateText = props.app == null ? 'Create' : 'Update';

  return (
    <Dialog open fullWidth onClose={() => props.setOpen(false)}>
      <FormContainer<App>
        defaultValues={{
          name: props.app?.name ?? '',
          description: props.app?.description ?? '',
        }}
        onSuccess={(formData) => submit(formData)}>
        <DialogTitle>{createOrUpdateText} App</DialogTitle>
        <DialogContent>
          {requestError != '' ? <Alert severity="error">{requestError}</Alert> : null}
          <FormControl margin="normal" fullWidth>
            <TextFieldElement
              fullWidth
              id="outlined-basic"
              label="Name"
              name="name"
              variant="outlined"
              validation={{
                maxLength: 255,
                pattern: new RegExp(accessConfig.NAME_VALIDATION_PATTERN),
              }}
              disabled={props.access_app}
              parseError={(error) => {
                if (error?.message != '') {
                  return error?.message ?? '';
                }
                if (error.type == 'maxLength') {
                  return 'Name can be at most 255 characters in length';
                }
                if (error.type == 'pattern') {
                  return (
                    accessConfig.NAME_VALIDATION_ERROR + ' Regex to match: ' + accessConfig.NAME_VALIDATION_PATTERN
                  );
                }

                return '';
              }}
              required
            />
          </FormControl>
          <FormControl margin="normal" fullWidth>
            <TextFieldElement
              label="Description"
              name="description"
              multiline
              rows={4}
              validation={{maxLength: 1024}}
              disabled={props.access_app}
              parseError={(error) => {
                if (error?.message != '') {
                  return error?.message ?? '';
                }
                if (error.type == 'maxLength') {
                  return 'Name can be at most 1024 characters in length';
                }
                return '';
              }}
            />
          </FormControl>
          <FormControl margin="normal" fullWidth>
            <Autocomplete
              multiple
              id="tags-outlined"
              options={tagSearchOptions}
              value={selectedTags}
              getOptionLabel={(option) => option.name}
              onInputChange={(event, newInputValue) => setTagSearchInput(newInputValue)}
              onChange={(event, newValue) => {
                setSelectedTags(newValue);
              }}
              renderTags={(value: Tag[], getTagProps) =>
                value.map((option: Tag, index: number) => (
                  <Chip variant="outlined" label={option.name} {...getTagProps({index})} />
                ))
              }
              renderInput={(params) => <TextField {...params} label="Tags" placeholder="Tags" />}
            />
          </FormControl>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => props.setOpen(false)}>Cancel</Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? <CircularProgress size={24} /> : createOrUpdateText}
          </Button>
        </DialogActions>
      </FormContainer>
    </Dialog>
  );
}

interface CreateUpdateAppProps {
  currentUser: OktaUser;
  app?: App;
}

export default function CreateUpdateApp(props: CreateUpdateAppProps) {
  const [open, setOpen] = React.useState(false);

  const create = props.app == null;
  const access_app = props.app?.name == ACCESS_APP_RESERVED_NAME;

  if (create) {
    if (!isAccessAdmin(props.currentUser)) {
      return null;
    }
  } else {
    if (!(isAccessAdmin(props.currentUser) || isAppOwnerGroupOwner(props.currentUser, props.app?.id ?? ''))) {
      return null;
    }
  }

  return (
    <>
      <AppButton setOpen={setOpen} app={props.app}></AppButton>
      {open ? (
        <AppDialog
          currentUser={props.currentUser}
          setOpen={setOpen}
          app={props.app}
          access_app={access_app}></AppDialog>
      ) : null}
    </>
  );
}
