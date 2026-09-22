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
import Divider from '@mui/material/Divider';
import EditIcon from '@mui/icons-material/Edit';
import FormControl from '@mui/material/FormControl';
import Grid from '@mui/material/Grid';
import IconButton from '@mui/material/IconButton';
import Typography from '@mui/material/Typography';

import {CheckboxElement, FormContainer, RadioButtonGroup, TextFieldElement} from 'react-hook-form-mui';
import {useWatch} from 'react-hook-form';

import {
  useTagsCreate,
  useTagByIdPut,
  TagsCreateError,
  TagByIdPutError,
  TagsCreateVariables,
  TagByIdPutVariables,
} from '../../api/apiComponents';
import NumberInput from '../../components/NumberInput';
import {ConstraintHelpButton, ConstraintHelpRegion} from '../../components/ConstraintHelp';
import {
  CONSTRAINT_ROW_LABELS,
  DISALLOW_SELF_ADD_MEMBERSHIP,
  DISALLOW_SELF_ADD_OWNERSHIP,
  MEMBER_TIME_LIMIT,
  OWNER_TIME_LIMIT,
  REQUIRE_MEMBER_REASON,
  REQUIRE_OWNER_REASON,
  SCOPE_LABELS,
  SCOPE_SUMMARIES,
  constraintDetail,
  constraintLabel,
  constraintSummary,
} from '../../constraintCopy';
import {propagationConflictMessage, selfAddRestrictionAvailable} from './propagationRules';
import {TagSettings, tighteningEffects} from './tagChanges';
import {OktaUserDetail, TagDetail} from '../../api/apiSchemas';
import {isAccessAdmin} from '../../authorization';
import accessConfig, {requireDescriptions} from '../../config/accessConfig';

interface TagButtonProps {
  setOpen(open: boolean): any;
  tag?: TagDetail;
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
  ownerReason?: boolean;
  memberReason?: boolean;
  ownerAdd?: boolean;
  memberAdd?: boolean;
  propagateToRoles: string;
}

interface TagDialogProps {
  currentUser: OktaUserDetail;
  setOpen(open: boolean): any;
  tag?: TagDetail;
}

// The scope choice is the form's `propagateToRoles` field, named for what each
// option does rather than for the flag it sets: "Propagate these constraints to
// roles?" can only be answered by someone who already knows what propagation
// means, which is the question the control is there to settle.
const SCOPE_ROLES = 'yes';
const SCOPE_GROUPS_ONLY = 'no';

/**
 * Where the tag's constraints apply, and why the narrow option may be unavailable.
 *
 * Asked of the value the control would take: the message names whichever
 * restrictions are keeping the narrow scope out of reach, or is null when none are.
 */
function ScopeChoice() {
  const ownerAdd = useWatch<CreateTagForm>({name: 'ownerAdd'});
  const memberAdd = useWatch<CreateTagForm>({name: 'memberAdd'});
  const conflict = propagationConflictMessage({
    propagateToRoles: SCOPE_GROUPS_ONLY,
    ownerAdd: ownerAdd as boolean | undefined,
    memberAdd: memberAdd as boolean | undefined,
  });
  return (
    <Box
      sx={{
        backgroundColor: (theme) => theme.palette.action.hover,
        borderRadius: 1,
        padding: '10px 12px',
        marginTop: '12px',
      }}>
      <Typography variant="body2" sx={{fontWeight: 'medium'}}>
        Constraint scope
      </Typography>
      <RadioButtonGroup
        name="propagateToRoles"
        helperText={conflict ?? undefined}
        options={[
          {id: SCOPE_ROLES, label: `${SCOPE_LABELS.roles}: ${SCOPE_SUMMARIES.roles}`},
          {
            id: SCOPE_GROUPS_ONLY,
            label: `${SCOPE_LABELS.groupsOnly}: ${SCOPE_SUMMARIES.groupsOnly}`,
            disabled: conflict !== null,
          },
        ]}
      />
    </Box>
  );
}

/**
 * One side of a constraint row: the control, its help button, and its helper line.
 *
 * The button sits on the control it explains rather than on the row label, so each
 * one answers for a single constraint. `onHelp` lifts the open state to the row,
 * which owns the region: the prose needs both columns, and a region inside this
 * cell would stretch its neighbour.
 */
function ConstraintCell({
  constraint,
  propagates,
  expanded,
  onHelp,
  regionId,
  children,
}: {
  constraint: string;
  propagates: boolean;
  expanded: boolean;
  onHelp: () => void;
  regionId: string;
  children: React.ReactNode;
}) {
  return (
    <>
      <Box sx={{display: 'flex', alignItems: 'center', gap: '4px'}}>
        <Box sx={{flex: 1, minWidth: 0}}>{children}</Box>
        <ConstraintHelpButton
          label={constraintLabel(constraint)}
          expanded={expanded}
          onToggle={onHelp}
          regionId={regionId}
        />
      </Box>
      <Typography variant="caption" color="text.secondary" sx={{display: 'block', marginTop: '2px'}}>
        {constraintSummary(constraint, propagates)}
      </Typography>
    </>
  );
}

/**
 * One row of the matrix: a setting, its membership and ownership controls, and
 * whichever side's help is open.
 *
 * At most one side is open at a time. Two open regions would push the row's
 * controls far apart, and the question a reader has is about the control they just
 * clicked, not both.
 */
function ConstraintRow({
  label,
  memberConstraint,
  ownerConstraint,
  propagates,
  memberControl,
  ownerControl,
}: {
  label: string;
  memberConstraint: string;
  ownerConstraint: string;
  propagates: boolean;
  memberControl: React.ReactNode;
  ownerControl: React.ReactNode;
}) {
  const [openSide, setOpenSide] = React.useState<string | null>(null);
  const toggle = (constraint: string) => setOpenSide((current) => (current === constraint ? null : constraint));
  const regionId = `constraint-help-${openSide ?? label}`;
  return (
    <>
      <Grid item xs={3} sx={{paddingTop: '20px !important'}}>
        <Typography variant="body2">{label}</Typography>
      </Grid>
      <Grid item xs={4.5}>
        <ConstraintCell
          constraint={memberConstraint}
          propagates={propagates}
          expanded={openSide === memberConstraint}
          onHelp={() => toggle(memberConstraint)}
          regionId={`constraint-help-${memberConstraint}`}>
          {memberControl}
        </ConstraintCell>
      </Grid>
      <Grid item xs={4.5}>
        <ConstraintCell
          constraint={ownerConstraint}
          propagates={propagates}
          expanded={openSide === ownerConstraint}
          onHelp={() => toggle(ownerConstraint)}
          regionId={`constraint-help-${ownerConstraint}`}>
          {ownerControl}
        </ConstraintCell>
      </Grid>
      {openSide != null && (
        <Grid item xs={12} sx={{paddingTop: '0 !important'}}>
          <ConstraintHelpRegion
            expanded
            regionId={regionId}
            sections={[{label: constraintLabel(openSide), paragraphs: constraintDetail(openSide, propagates)}]}
          />
        </Grid>
      )}
    </>
  );
}

/** The constraint matrix: three settings, each with a membership and an owner side. */
function ConstraintMatrix({
  setDaysMember,
  defaultDaysMember,
  setDaysOwner,
  defaultDaysOwner,
}: {
  setDaysMember: (value: number | undefined) => void;
  defaultDaysMember: number | undefined;
  setDaysOwner: (value: number | undefined) => void;
  defaultDaysOwner: number | undefined;
}) {
  const propagates = useWatch<CreateTagForm>({name: 'propagateToRoles'}) !== SCOPE_GROUPS_ONLY;
  // A self-add restriction and the narrower scope are not independently
  // configurable (`propagationRules.ts` carries the reason), so the checkbox is
  // disabled rather than rejected after the fact. The helper line says why.
  const selfAddAvailable = selfAddRestrictionAvailable(propagates ? SCOPE_ROLES : SCOPE_GROUPS_ONLY);

  return (
    <Grid container columnSpacing={2} rowSpacing={2} sx={{marginTop: '4px'}}>
      <Grid item xs={3} />
      <Grid item xs={4.5}>
        <Typography variant="caption" color="text.secondary">
          Membership
        </Typography>
      </Grid>
      <Grid item xs={4.5}>
        <Typography variant="caption" color="text.secondary">
          Ownership
        </Typography>
      </Grid>

      <ConstraintRow
        label={CONSTRAINT_ROW_LABELS.timeLimit}
        memberConstraint={MEMBER_TIME_LIMIT}
        ownerConstraint={OWNER_TIME_LIMIT}
        propagates={propagates}
        memberControl={
          <NumberInput
            label="Membership time limit in days"
            setValue={setDaysMember}
            min={1}
            default={defaultDaysMember}
            endAdornment="days"
          />
        }
        ownerControl={
          <NumberInput
            label="Ownership time limit in days"
            setValue={setDaysOwner}
            min={1}
            default={defaultDaysOwner}
            endAdornment="days"
          />
        }
      />

      <ConstraintRow
        label={CONSTRAINT_ROW_LABELS.requireReason}
        memberConstraint={REQUIRE_MEMBER_REASON}
        ownerConstraint={REQUIRE_OWNER_REASON}
        propagates={propagates}
        memberControl={<CheckboxElement name="memberReason" label="Yes" />}
        ownerControl={<CheckboxElement name="ownerReason" label="Yes" />}
      />

      <ConstraintRow
        label={CONSTRAINT_ROW_LABELS.disallowSelfAdd}
        memberConstraint={DISALLOW_SELF_ADD_MEMBERSHIP}
        ownerConstraint={DISALLOW_SELF_ADD_OWNERSHIP}
        propagates={propagates}
        memberControl={<CheckboxElement name="memberAdd" label="Yes" disabled={!selfAddAvailable} />}
        ownerControl={<CheckboxElement name="ownerAdd" label="Yes" disabled={!selfAddAvailable} />}
      />
    </Grid>
  );
}

/**
 * What saving will do to access that already exists.
 *
 * Shown only when something tightens. Loosening a tag -- raising a limit, clearing
 * a requirement, narrowing its scope -- changes nothing that is already granted, so
 * a warning there would cry wolf and teach an admin to skip reading it.
 */
function BlastRadius({tag, daysMember, daysOwner}: {tag?: TagDetail; daysMember?: number; daysOwner?: number}) {
  // Watched per field rather than as a whole: `useWatch()` with no name does not
  // report the form's values here, and a silently-empty read made the warning
  // describe a tag nobody was editing.
  const propagateToRoles = useWatch<CreateTagForm>({name: 'propagateToRoles'});
  const memberReason = useWatch<CreateTagForm>({name: 'memberReason'});
  const ownerReason = useWatch<CreateTagForm>({name: 'ownerReason'});
  const memberAdd = useWatch<CreateTagForm>({name: 'memberAdd'});
  const ownerAdd = useWatch<CreateTagForm>({name: 'ownerAdd'});
  const apps = tag?.active_app_tags?.length ?? 0;
  const groups = tag?.active_group_tags?.length ?? 0;
  if (tag == null || (apps === 0 && groups === 0)) {
    return null;
  }

  const stored = tag.constraints ?? {};
  const savedDays = (key: string) => (stored[key] ? Math.floor(stored[key] / 86400) : undefined);
  const before: TagSettings = {
    memberTimeLimitDays: savedDays(MEMBER_TIME_LIMIT),
    ownerTimeLimitDays: savedDays(OWNER_TIME_LIMIT),
    requireMemberReason: stored[REQUIRE_MEMBER_REASON] === true,
    requireOwnerReason: stored[REQUIRE_OWNER_REASON] === true,
    disallowSelfAddMembership: stored[DISALLOW_SELF_ADD_MEMBERSHIP] === true,
    disallowSelfAddOwnership: stored[DISALLOW_SELF_ADD_OWNERSHIP] === true,
    propagatesToRoles: tag.propagate_to_roles ?? true,
  };
  const after: TagSettings = {
    memberTimeLimitDays: daysMember,
    ownerTimeLimitDays: daysOwner,
    requireMemberReason: memberReason === true,
    requireOwnerReason: ownerReason === true,
    disallowSelfAddMembership: memberAdd === true,
    disallowSelfAddOwnership: ownerAdd === true,
    propagatesToRoles: propagateToRoles !== SCOPE_GROUPS_ONLY,
  };

  const effects = tighteningEffects(before, after);
  if (effects.length === 0) {
    return null;
  }

  const where = [
    apps > 0 ? `${apps} app${apps === 1 ? '' : 's'}` : '',
    groups > 0 ? `${groups} group${groups === 1 ? '' : 's'}` : '',
  ]
    .filter(Boolean)
    .join(' and ');

  return (
    <Alert severity="warning" sx={{marginTop: '16px'}}>
      <Typography variant="body2" sx={{fontWeight: 'medium'}}>
        This tightens a tag applied to {where}.
      </Typography>
      {effects.map((effect) => (
        <Typography key={effect} variant="body2">
          {effect}
        </Typography>
      ))}
    </Alert>
  );
}

function TagDialog(props: TagDialogProps) {
  const navigate = useNavigate();

  const [requestError, setRequestError] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);
  const defaultDaysMember =
    props.tag && props.tag.constraints && props.tag.constraints[MEMBER_TIME_LIMIT]
      ? Math.floor(props.tag.constraints[MEMBER_TIME_LIMIT] / 86400)
      : undefined;
  const [daysMember, setDaysMember] = React.useState<number | undefined>(defaultDaysMember);
  const defaultDaysOwner =
    props.tag && props.tag.constraints && props.tag.constraints[OWNER_TIME_LIMIT]
      ? Math.floor(props.tag.constraints[OWNER_TIME_LIMIT] / 86400)
      : undefined;
  const [daysOwner, setDaysOwner] = React.useState<number | undefined>(defaultDaysOwner);

  const complete = (
    completedTag: TagDetail | undefined,
    error: TagsCreateError | TagByIdPutError | null,
    variables: TagsCreateVariables | TagByIdPutVariables,
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

  const createTag = useTagsCreate({
    onSettled: complete,
  });
  const updateTag = useTagByIdPut({
    onSettled: complete,
  });

  const submit = (tagForm: CreateTagForm) => {
    setSubmitting(true);

    const tag = {
      name: tagForm.name,
      description: tagForm.description,
      enabled: tagForm.enabled == 'enabled',
      propagate_to_roles: tagForm.propagateToRoles != SCOPE_GROUPS_ONLY,
    } as TagDetail;

    const constraints: Record<string, number | boolean> = {};
    if (daysMember) {
      constraints[MEMBER_TIME_LIMIT] = daysMember * 86400;
    }
    if (daysOwner) {
      constraints[OWNER_TIME_LIMIT] = daysOwner * 86400;
    }
    constraints[REQUIRE_OWNER_REASON] = tagForm.ownerReason === true;
    constraints[REQUIRE_MEMBER_REASON] = tagForm.memberReason === true;
    constraints[DISALLOW_SELF_ADD_OWNERSHIP] = tagForm.ownerAdd === true;
    constraints[DISALLOW_SELF_ADD_MEMBERSHIP] = tagForm.memberAdd === true;

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
  const flag = (key: string) => (props.tag?.constraints ? props.tag.constraints[key] === true : false);

  return (
    <Dialog open fullWidth maxWidth="md" onClose={() => props.setOpen(false)}>
      <FormContainer<CreateTagForm>
        defaultValues={{
          name: props.tag?.name ?? '',
          description: props.tag?.description ?? '',
          enabled: props.tag ? (props.tag.enabled ? 'enabled' : 'disabled') : 'enabled',
          ownerReason: flag(REQUIRE_OWNER_REASON),
          memberReason: flag(REQUIRE_MEMBER_REASON),
          ownerAdd: flag(DISALLOW_SELF_ADD_OWNERSHIP),
          memberAdd: flag(DISALLOW_SELF_ADD_MEMBERSHIP),
          // `?? true` rather than a bare truthiness check: the field is
          // optional in the generated type and the server default is `true`,
          // so an absent value must prefill the wider scope -- otherwise
          // opening and saving an older tag silently narrows it.
          propagateToRoles: props.tag
            ? props.tag.propagate_to_roles ?? true
              ? SCOPE_ROLES
              : SCOPE_GROUPS_ONLY
            : SCOPE_ROLES,
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
                  rules={{
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
              <FormControl fullWidth sx={{marginTop: '14px'}}>
                <RadioButtonGroup
                  name="enabled"
                  row
                  options={[
                    {id: 'enabled', label: 'Enabled'},
                    {id: 'disabled', label: 'Disabled'},
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
              rows={3}
              rules={{maxLength: 1024}}
              parseError={(error) => {
                if (error?.message != '') {
                  return error?.message ?? '';
                }
                if (error.type == 'maxLength') {
                  return 'Description can be at most 1024 characters in length';
                }
                return '';
              }}
              required={requireDescriptions}
            />
          </FormControl>

          <Divider sx={{margin: '8px 0'}} />
          <Typography variant="h6" sx={{fontSize: 18}}>
            Constraints
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Leave a box unchecked or a limit blank to impose nothing.
          </Typography>

          <ScopeChoice />

          <ConstraintMatrix
            setDaysMember={setDaysMember}
            defaultDaysMember={defaultDaysMember}
            setDaysOwner={setDaysOwner}
            defaultDaysOwner={defaultDaysOwner}
          />

          <BlastRadius tag={props.tag} daysMember={daysMember} daysOwner={daysOwner} />
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
  currentUser: OktaUserDetail;
  tag?: TagDetail;
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
