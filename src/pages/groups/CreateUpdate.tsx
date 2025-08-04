import * as React from 'react';
import {useNavigate} from 'react-router-dom';
import Alert from '@mui/material/Alert';
import Autocomplete from '@mui/material/Autocomplete';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import CircularProgress from '@mui/material/CircularProgress';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import EditIcon from '@mui/icons-material/Edit';
import FormControl from '@mui/material/FormControl';
import GroupAddIcon from '@mui/icons-material/GroupAdd';
import IconButton from '@mui/material/IconButton';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';

import {FormContainer, SelectElement, AutocompleteElement, TextFieldElement} from 'react-hook-form-mui';

import {
  useGetApps,
  useGetTags,
  useCreateGroup,
  usePutGroupById,
  CreateGroupError,
  PutGroupByIdError,
  CreateGroupVariables,
  PutGroupByIdVariables,
} from '../../api/apiComponents';
import {PolymorphicGroup, AppGroup, App, OktaUser, Tag, OktaGroupTagMap} from '../../api/apiSchemas';
import {canManageGroup, isAccessAdmin, isAppOwnerGroupOwner} from '../../authorization';
import accessConfig from '../../config/accessConfig';

interface GroupButtonProps {
  defaultGroupType: 'okta_group' | 'app_group' | 'role_group';
  setOpen(open: boolean): any;
  group?: PolymorphicGroup;
}

function GroupButton(props: GroupButtonProps) {
  if (props.group == null) {
    return (
      <Button variant="contained" onClick={() => props.setOpen(true)} endIcon={<GroupAddIcon />}>
        {'Create ' + GROUP_TYPE_ID_TO_LABELS[props.defaultGroupType]}
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

interface GroupDialogProps {
  currentUser: OktaUser;
  defaultGroupType: 'okta_group' | 'app_group' | 'role_group';
  setOpen(open: boolean): any;
  app?: App;
  group?: PolymorphicGroup;
  app_owner_group?: boolean;
}

const GROUP_TYPE_ID_TO_LABELS: Record<string, string> = {
  okta_group: 'Group',
  app_group: 'App Group',
  role_group: 'Role',
} as const;

const GROUP_TYPE_OPTIONS = Object.entries(GROUP_TYPE_ID_TO_LABELS).map(([id, label], index) => ({
  id: id,
  label: label,
}));

const APP_GROUP_PREFIX = 'App-';
const APP_NAME_APP_GROUP_SEPARATOR = '-';
const ROLE_GROUP_PREFIX = 'Role-';

function GroupDialog(props: GroupDialogProps) {
  const navigate = useNavigate();

  const defaultGroupType = props.app != null ? 'app_group' : props.group?.type ?? props.defaultGroupType;
  const [groupType, setGroupType] = React.useState(defaultGroupType);
  const defaultTags =
    props.group && props.group.active_group_tags && props.group.active_group_tags.length > 0
      ? props.group.active_group_tags
          .filter((tagMap: OktaGroupTagMap) => tagMap.active_app_tag_mapping == null)
          .map((tagMap: OktaGroupTagMap) => tagMap.active_tag!)
      : [];
  const [selectedTags, setSelectedTags] = React.useState<Array<Tag>>(defaultTags);
  const [appSearchInput, setAppSearchInput] = React.useState('');
  const [tagSearchInput, setTagSearchInput] = React.useState('');
  const initialAppName = props.app?.name ?? (props.group as AppGroup)?.app?.name ?? '';
  const [appName, setAppName] = React.useState(initialAppName);
  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);

  const complete = (
    completedGroup: PolymorphicGroup | undefined,
    error: CreateGroupError | PutGroupByIdError | null,
    variables: CreateGroupVariables | PutGroupByIdVariables,
    context: any,
  ) => {
    setSubmitting(false);
    if (error != null) {
      setRequestError(error.payload.toString());
    } else {
      props.setOpen(false);
      if ((props.group?.name ?? '') == (completedGroup?.name ?? '')) {
        navigate(0);
      } else if (completedGroup?.type == 'app_group' && props.app != null) {
        navigate(0);
      } else {
        navigate('/groups/' + encodeURIComponent(completedGroup?.name ?? ''));
      }
    }
  };

  const createGroup = useCreateGroup({
    onSettled: complete,
  });
  const updateGroup = usePutGroupById({
    onSettled: complete,
  });

  const {data: appSearchData} = useGetApps({
    queryParams: {
      page: 0,
      per_page: 10,
      q: appSearchInput,
    },
  });
  const appSearchOptions = appSearchData?.results ?? [];

  const {data: tagSearchData} = useGetTags({
    queryParams: {
      page: 0,
      per_page: 10,
      q: tagSearchInput,
    },
  });
  const tagSearchOptions = tagSearchData?.results ?? [];

  const submit = (group: PolymorphicGroup) => {
    setSubmitting(true);

    if (props.group) {
      group.tags_to_add = selectedTags.filter((x) => !defaultTags.includes(x)).map((tag: Tag) => tag.id);
      group.tags_to_remove = defaultTags.filter((x) => !selectedTags.includes(x)).map((tag: Tag) => tag.id);
    } else {
      group.tags_to_add = selectedTags.map((tag: Tag) => tag.id);
    }

    switch (group.type) {
      case 'okta_group':
        break;
      case 'role_group':
        group.name = ROLE_GROUP_PREFIX + group.name;
        break;
      case 'app_group':
        const appGroup = group as AppGroup;
        appGroup.app_id = appGroup.app?.id ?? '';
        appGroup.name = APP_GROUP_PREFIX + (appGroup.app?.name ?? '') + APP_NAME_APP_GROUP_SEPARATOR + appGroup.name;
        break;
    }
    delete (group as AppGroup).app;

    if (props.group == null) {
      createGroup.mutate({body: group});
    } else {
      updateGroup.mutate({
        body: group,
        pathParams: {groupId: props.group?.id ?? ''},
      });
    }
  };

  const createOrUpdateText = props.group == null ? 'Create' : 'Update';

  return (
    <Dialog open onClose={() => props.setOpen(false)}>
      <FormContainer<PolymorphicGroup>
        defaultValues={
          props.app != null || props.group?.type == 'app_group'
            ? {
                type: defaultGroupType,
                app: props.app ?? (props.group as AppGroup)?.app ?? {},
                name:
                  props.group?.name.substring(
                    (APP_GROUP_PREFIX + initialAppName + APP_NAME_APP_GROUP_SEPARATOR).length,
                  ) ?? '',
                description: props.group?.description ?? '',
              }
            : {
                type: defaultGroupType,
                name:
                  props.group?.type == 'role_group'
                    ? props.group?.name.substring(ROLE_GROUP_PREFIX.length)
                    : props.group?.name ?? '',
                description: props.group?.description ?? '',
              }
        }
        onSuccess={(formData) => submit(formData)}>
        <DialogTitle>
          {createOrUpdateText} {GROUP_TYPE_ID_TO_LABELS[defaultGroupType]}
        </DialogTitle>
        <DialogContent>
          {requestError != '' ? <Alert severity="error">{requestError}</Alert> : null}
          <FormControl size="small" margin="normal" fullWidth>
            <SelectElement
              label="Type"
              name="type"
              options={GROUP_TYPE_OPTIONS}
              onChange={(value) => setGroupType(value)}
              disabled={props.app != null || !isAccessAdmin(props.currentUser) || props.app_owner_group}
              required
            />
          </FormControl>
          {groupType == 'app_group' ? (
            <FormControl margin="normal" fullWidth required>
              <AutocompleteElement
                label="App"
                name="app"
                options={appSearchOptions}
                required
                autocompleteProps={{
                  disabled: props.app != null || !isAccessAdmin(props.currentUser) || props.app_owner_group,
                  getOptionLabel: (option) => option.name,
                  isOptionEqualToValue: (option, value) => option.id == value.id,
                  onInputChange: (event, newInputValue) => setAppSearchInput(newInputValue),
                  onChange: (event, value) => setAppName(value!.name),
                }}
              />
            </FormControl>
          ) : null}
          <FormControl margin="normal" fullWidth>
            <Box
              sx={{
                display: 'flex',
                justifyContent: 'flex-start',
                flexDirection: 'row',
                alignItems: 'center',
              }}>
              {groupType == 'app_group' || groupType == 'role_group' ? (
                <Box sx={{mx: 1}}>
                  <Typography noWrap={true} variant="h6">
                    {groupType == 'role_group'
                      ? ROLE_GROUP_PREFIX
                      : APP_GROUP_PREFIX + (appName == '' ? '<App>' : appName) + APP_NAME_APP_GROUP_SEPARATOR}
                  </Typography>
                </Box>
              ) : null}
              <TextFieldElement
                fullWidth
                id="outlined-basic"
                label="Name"
                name="name"
                variant="outlined"
                disabled={props.app_owner_group}
                validation={{
                  maxLength: 255,
                  pattern: new RegExp(accessConfig.NAME_VALIDATION_PATTERN),
                }}
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
            </Box>
          </FormControl>
          <FormControl margin="normal" fullWidth>
            <TextFieldElement
              label="Description"
              name="description"
              multiline
              rows={4}
              validation={{maxLength: 1024}}
              disabled={props.app_owner_group}
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

interface CreateUpdateGroupProps {
  currentUser: OktaUser;
  defaultGroupType?: 'okta_group' | 'app_group' | 'role_group';
  app?: App;
  group?: PolymorphicGroup;
}

export default function CreateUpdateGroup(props: CreateUpdateGroupProps) {
  const [open, setOpen] = React.useState(false);

  const defaultGroupType = props.defaultGroupType ?? 'okta_group';
  const create = props.group == null;
  const owner_app_group = props.group?.type == 'app_group' && (props.group as AppGroup).is_owner;

  if (create) {
    if (!(isAccessAdmin(props.currentUser) || isAppOwnerGroupOwner(props.currentUser, props.app?.id ?? ''))) {
      return null;
    }
  } else {
    if (
      props.group?.deleted_at != null ||
      !canManageGroup(props.currentUser, props.group) ||
      props.group?.is_managed == false
    ) {
      return null;
    }
  }

  return (
    <>
      <GroupButton defaultGroupType={defaultGroupType} setOpen={setOpen} group={props.group}></GroupButton>
      {open ? (
        <GroupDialog
          currentUser={props.currentUser}
          defaultGroupType={defaultGroupType}
          setOpen={setOpen}
          group={props.group}
          app_owner_group={owner_app_group}
          app={props.app}></GroupDialog>
      ) : null}
    </>
  );
}
