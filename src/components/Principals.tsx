import * as React from 'react';
import {Link as RouterLink} from 'react-router-dom';
import Autocomplete from '@mui/material/Autocomplete';
import Chip from '@mui/material/Chip';
import FormControl from '@mui/material/FormControl';
import Grid from '@mui/material/Grid';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import UserIcon from '@mui/icons-material/Person';
import RoleIcon from '@mui/icons-material/Diversity3';

import {useRoles, useUsers} from '../api/apiComponents';
import {OktaUserSummary, RoleGroupListItem} from '../api/apiSchemas';
import {displayUserName} from '../helpers';

export type RoleOption = Pick<RoleGroupListItem, 'id' | 'name'>;

export const ROLES_IN_ROLE_ERROR = 'Roles cannot be added to other Roles.';

/** Whether two id lists hold the same ids, ignoring order. */
export function sameIds(a: string[], b: string[]): boolean {
  const set = new Set(a);
  return set.size === new Set(b).size && b.every((id) => set.has(id));
}

/** Picks the entries of `options` named by `ids`, in the order `ids` gives. */
export function pickByIds<T extends {id: string}>(ids: string[] | undefined, options: T[] | undefined): T[] {
  const byId = new Map((options ?? []).map((option) => [option.id, option]));
  return (ids ?? []).flatMap((id) => {
    const option = byId.get(id);
    return option ? [option] : [];
  });
}

interface PrincipalChipsProps {
  users: OktaUserSummary[];
  roles: RoleOption[];
  struck?: boolean;
}

/**
 * Renders users and roles as chips linking to their pages.
 *
 * Args:
 *   users: The users to render.
 *   roles: The roles to render.
 *   struck: Render the chips crossed out, as values an approval replaced.
 */
export function PrincipalChips({users, roles, struck = false}: PrincipalChipsProps) {
  const sx = struck ? {textDecoration: 'line-through', opacity: 0.6} : undefined;
  return (
    <>
      {users.map((user) => (
        <Chip
          key={'user-' + user.id}
          icon={<UserIcon />}
          label={displayUserName(user)}
          component={RouterLink}
          to={`/users/${user.email.toLowerCase()}`}
          clickable
          size="small"
          variant="outlined"
          sx={sx}
        />
      ))}
      {roles.map((role) => (
        <Chip
          key={'role-' + role.id}
          icon={<RoleIcon />}
          label={role.name}
          component={RouterLink}
          to={`/roles/${role.name}`}
          clickable
          size="small"
          variant="outlined"
          sx={sx}
        />
      ))}
    </>
  );
}

interface PrincipalsFieldProps {
  label: string;
  users: OktaUserSummary[];
  roles: RoleOption[];
  onUsersChange: (users: OktaUserSummary[]) => void;
  onRolesChange: (roles: RoleOption[]) => void;
  rolesDisallowed: boolean;
  helperText?: string;
  error?: boolean;
}

/**
 * A pair of pickers naming the users and the roles to grant one kind of access.
 *
 * Args:
 *   label: What the grant is, such as "Owners" or "Members".
 *   users: The selected users.
 *   roles: The selected roles.
 *   onUsersChange: Called with the new user selection.
 *   onRolesChange: Called with the new role selection.
 *   rolesDisallowed: Whether the group is a role, which cannot take roles. The role picker is
 *     disabled while empty, and flags any roles already selected so they can be removed.
 *   helperText: Shown beneath the pickers.
 *   error: Flag both pickers and the helper text as invalid.
 */
export function PrincipalsField(props: PrincipalsFieldProps) {
  const [userSearchInput, setUserSearchInput] = React.useState('');
  const [roleSearchInput, setRoleSearchInput] = React.useState('');

  const {data: userSearchData} = useUsers({queryParams: {page: 1, size: 10, q: userSearchInput}});
  const {data: roleSearchData} = useRoles({queryParams: {page: 1, size: 10, q: roleSearchInput}});

  const rolesError = props.rolesDisallowed && props.roles.length > 0;

  return (
    <FormControl margin="normal" fullWidth>
      <Grid container spacing={2} sx={{alignItems: 'flex-start'}}>
        <Grid size={6}>
          <Autocomplete
            multiple
            options={userSearchData?.items ?? []}
            value={props.users}
            filterOptions={(x) => x}
            isOptionEqualToValue={(option, value) => option.id == value.id}
            getOptionLabel={(option) => displayUserName(option)}
            onInputChange={(event, value) => setUserSearchInput(value)}
            onChange={(event, value) => props.onUsersChange(value)}
            renderValue={(value, getItemProps) =>
              value.map((option, index) => (
                <Chip variant="outlined" label={displayUserName(option)} {...getItemProps({index})} />
              ))
            }
            renderInput={(params) => <TextField {...params} label={`${props.label}: users`} error={props.error} />}
          />
        </Grid>
        <Grid size={6}>
          <Autocomplete
            multiple
            options={(roleSearchData?.items ?? []) as RoleOption[]}
            value={props.roles}
            disabled={props.rolesDisallowed && !rolesError}
            filterOptions={(x) => x}
            isOptionEqualToValue={(option, value) => option.id == value.id}
            getOptionLabel={(option) => option.name}
            onInputChange={(event, value) => setRoleSearchInput(value)}
            onChange={(event, value) => props.onRolesChange(value)}
            renderValue={(value, getItemProps) =>
              value.map((option, index) => <Chip variant="outlined" label={option.name} {...getItemProps({index})} />)
            }
            renderInput={(params) => (
              <TextField
                {...params}
                label={`${props.label}: roles`}
                error={rolesError || props.error}
                helperText={props.rolesDisallowed ? ROLES_IN_ROLE_ERROR : undefined}
              />
            )}
          />
        </Grid>
      </Grid>
      {props.helperText && (
        <Typography variant="caption" sx={{color: props.error ? 'error.main' : 'text.secondary', mt: 0.5, ml: 1.75}}>
          {props.helperText}
        </Typography>
      )}
    </FormControl>
  );
}
