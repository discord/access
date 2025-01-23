import * as React from 'react';

import {useNavigate} from 'react-router-dom';

import Alert from '@mui/material/Alert';
import AddTagIcon from '@mui/icons-material/Discount';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import CircularProgress from '@mui/material/CircularProgress';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import EditIcon from '@mui/icons-material/Edit';
import FormControl from '@mui/material/FormControl';
import Grid from '@mui/material/Grid';
import IconButton from '@mui/material/IconButton';

import {ToggleButtonGroupElement, FormContainer, TextFieldElement} from 'react-hook-form-mui';

import {
  useCreateTag,
  usePutTagById,
  CreateTagError,
  PutTagByIdError,
  CreateTagVariables,
  PutTagByIdVariables,
} from '../../api/apiComponents';
import NumberInput from '../../components/NumberInput';
import {OktaUser, Tag} from '../../api/apiSchemas';
import {isAccessAdmin} from '../../authorization';
import accessConfig from '../../config/accessConfig';

interface TagButtonProps {
  setOpen(open: boolean): any;
  tag?: Tag;
}

function TagButton(props: TagButtonProps) {
  if (props.tag == null) {
    return (
      <Button variant="contained" onClick={() => props.setOpen(true)} endIcon={<AddTagIcon />}>
        Create Tag
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

interface CreateTagForm {
  name: string;
  description?: string;
  enabled?: string;
  customUntil?: string;
  ownerReason?: string;
  memberReason?: string;
  ownerAdd?: string;
  memberAdd?: string;
}

interface TagDialogProps {
  currentUser: OktaUser;
  setOpen(open: boolean): any;
  tag?: Tag;
}

function TagDialog(props: TagDialogProps) {
  const navigate = useNavigate();

  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);
  const defaultDaysMember =
    props.tag && props.tag.constraints && props.tag.constraints['member_time_limit']
      ? Math.floor(props.tag.constraints['member_time_limit'] / 86400)
      : undefined;
  const [daysMember, setDaysMember] = React.useState<number | undefined>(defaultDaysMember);
  const defaultDaysOwner =
    props.tag && props.tag.constraints && props.tag.constraints['owner_time_limit']
      ? Math.floor(props.tag.constraints['owner_time_limit'] / 86400)
      : undefined;
  const [daysOwner, setDaysOwner] = React.useState<number | undefined>(defaultDaysOwner);

  const complete = (
    completedTag: Tag | undefined,
    error: CreateTagError | PutTagByIdError | null,
    variables: CreateTagVariables | PutTagByIdVariables,
    context: any,
  ) => {
    setSubmitting(false);
    if (error != null) {
      setRequestError(error.payload.toString());
    } else {
      props.setOpen(false);
      if ((props.tag?.name ?? '') == (completedTag?.name ?? '')) {
        navigate(0);
      } else {
        navigate('/tags/' + encodeURIComponent(completedTag?.name ?? ''));
      }
    }
  };

  const createTag = useCreateTag({
    onSettled: complete,
  });
  const updateTag = usePutTagById({
    onSettled: complete,
  });

  const submit = (tagForm: CreateTagForm) => {
    setSubmitting(true);

    const tag = {
      name: tagForm.name,
      description: tagForm.description,
      enabled: tagForm.enabled == 'enabled',
    } as Tag;

    const constraints: Record<string, number | boolean> = {};
    if (daysMember) {
      constraints['member_time_limit'] = daysMember * 86400;
    }
    if (daysOwner) {
      constraints['owner_time_limit'] = daysOwner * 86400;
    }
    if (tagForm.ownerReason == 'yes') {
      constraints['require_owner_reason'] = true;
    } else {
      constraints['require_owner_reason'] = false;
    }
    if (tagForm.memberReason == 'yes') {
      constraints['require_member_reason'] = true;
    } else {
      constraints['require_member_reason'] = false;
    }
    if (tagForm.ownerAdd == 'yes') {
      constraints['disallow_self_add_ownership'] = true;
    } else {
      constraints['disallow_self_add_ownership'] = false;
    }
    if (tagForm.memberAdd == 'yes') {
      constraints['disallow_self_add_membership'] = true;
    } else {
      constraints['disallow_self_add_membership'] = false;
    }

    tag.constraints = constraints;

    if (props.tag == null) {
      createTag.mutate({body: tag});
    } else {
      updateTag.mutate({
        body: tag,
        pathParams: {tagId: props.tag?.id ?? ''},
      });
    }
  };

  const createOrUpdateText = props.tag == null ? 'Create' : 'Update';

  return (
    <Dialog open fullWidth onClose={() => props.setOpen(false)}>
      <FormContainer<CreateTagForm>
        defaultValues={{
          name: props.tag?.name ?? '',
          description: props.tag?.description ?? '',
          enabled: props.tag ? (props.tag.enabled ? 'enabled' : 'disabled') : 'enabled',
          ownerReason:
            props.tag && props.tag.constraints ? (props.tag.constraints.require_owner_reason ? 'yes' : 'no') : 'no',
          memberReason:
            props.tag && props.tag.constraints ? (props.tag.constraints.require_member_reason ? 'yes' : 'no') : 'no',
          ownerAdd:
            props.tag && props.tag.constraints
              ? props.tag.constraints.disallow_self_add_ownership
                ? 'yes'
                : 'no'
              : 'no',
          memberAdd:
            props.tag && props.tag.constraints
              ? props.tag.constraints.disallow_self_add_membership
                ? 'yes'
                : 'no'
              : 'no',
        }}
        onSuccess={(formData) => submit(formData)}>
        <DialogTitle>{createOrUpdateText} Tag</DialogTitle>
        <DialogContent>
          {requestError != '' ? <Alert severity="error">{requestError}</Alert> : null}
          <Grid container spacing={1}>
            <Grid item xs={8}>
              <FormControl fullWidth sx={{marginTop: '14px'}}>
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
            </Grid>
            <Grid item xs={4}>
              <FormControl fullWidth sx={{marginTop: '18px'}}>
                <ToggleButtonGroupElement
                  name="enabled"
                  enforceAtLeastOneSelected
                  exclusive
                  required
                  options={[
                    {
                      id: 'enabled',
                      label: 'Enabled',
                    },
                    {
                      id: 'disabled',
                      label: 'Disabled',
                    },
                  ]}
                />
              </FormControl>
            </Grid>
          </Grid>
          <FormControl margin="normal" fullWidth>
            <TextFieldElement
              label="Description"
              name="description"
              multiline
              rows={4}
              validation={{maxLength: 1024}}
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
          <Box sx={{fontWeight: 'medium', fontSize: 18, margin: '8px 0 4px 0'}}>Optional constraints</Box>
          <Grid container spacing={1}>
            <Grid item xs={6}>
              <FormControl fullWidth>
                <Box sx={{marginLeft: '3px'}}>Owner time limit:</Box>
                <NumberInput
                  label={'days'}
                  setValue={setDaysOwner}
                  min={1}
                  default={defaultDaysOwner ? defaultDaysOwner : undefined}
                  endAdornment="days"
                />
              </FormControl>
            </Grid>
            <Grid item xs={6}>
              <FormControl fullWidth>
                <Box sx={{marginLeft: '3px'}}>Member time limit:</Box>
                <NumberInput
                  label={'days'}
                  setValue={setDaysMember}
                  min={1}
                  default={defaultDaysMember ? defaultDaysMember : undefined}
                  endAdornment="days"
                />
              </FormControl>
            </Grid>
          </Grid>
          <Grid container spacing={1}>
            <Grid item xs={6}>
              <FormControl fullWidth sx={{marginTop: '18px'}}>
                <Box sx={{marginLeft: '3px'}}>Require ownership justification?:</Box>
                <ToggleButtonGroupElement
                  name="ownerReason"
                  enforceAtLeastOneSelected
                  exclusive
                  required
                  options={[
                    {
                      id: 'yes',
                      label: 'Yes',
                    },
                    {
                      id: 'no',
                      label: 'No',
                    },
                  ]}
                />
              </FormControl>
            </Grid>
            <Grid item xs={6}>
              <FormControl fullWidth sx={{marginTop: '18px'}}>
                <Box sx={{marginLeft: '3px'}}>Require membership justification?:</Box>
                <ToggleButtonGroupElement
                  name="memberReason"
                  enforceAtLeastOneSelected
                  exclusive
                  required
                  options={[
                    {
                      id: 'yes',
                      label: 'Yes',
                    },
                    {
                      id: 'no',
                      label: 'No',
                    },
                  ]}
                />
              </FormControl>
            </Grid>
          </Grid>
          <Grid container spacing={1}>
            <Grid item xs={6}>
              <FormControl fullWidth sx={{marginTop: '18px'}}>
                <Box sx={{marginLeft: '3px'}}>Disallow owners adding selves as owners?:</Box>
                <ToggleButtonGroupElement
                  name="ownerAdd"
                  enforceAtLeastOneSelected
                  exclusive
                  required
                  options={[
                    {
                      id: 'yes',
                      label: 'Yes',
                    },
                    {
                      id: 'no',
                      label: 'No',
                    },
                  ]}
                />
              </FormControl>
            </Grid>
            <Grid item xs={6}>
              <FormControl fullWidth sx={{marginTop: '18px'}}>
                <Box sx={{marginLeft: '3px'}}>Disallow owners adding selves as members?:</Box>
                <ToggleButtonGroupElement
                  name="memberAdd"
                  enforceAtLeastOneSelected
                  exclusive
                  required
                  options={[
                    {
                      id: 'yes',
                      label: 'Yes',
                    },
                    {
                      id: 'no',
                      label: 'No',
                    },
                  ]}
                />
              </FormControl>
            </Grid>
          </Grid>
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

interface CreateUpdateTagProps {
  currentUser: OktaUser;
  tag?: Tag;
}

export default function CreateUpdateTag(props: CreateUpdateTagProps) {
  const [open, setOpen] = React.useState(false);

  if ((props.tag && props.tag.deleted_at != null) || !isAccessAdmin(props.currentUser)) {
    return null;
  }

  return (
    <>
      <TagButton setOpen={setOpen} tag={props.tag}></TagButton>
      {open ? <TagDialog currentUser={props.currentUser} setOpen={setOpen} tag={props.tag}></TagDialog> : null}
    </>
  );
}
