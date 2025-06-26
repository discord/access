import {Launch} from '@mui/icons-material';
import {Autocomplete, AutocompleteProps, Box, Grid, IconButton, Stack, TextField, Typography} from '@mui/material';

import * as React from 'react';
import {useNavigate} from 'react-router-dom';

export function renderUserOption(props: React.HTMLAttributes<HTMLLIElement>, option: any) {
  const [displayName, email] = option.split(';');
  return (
    <li {...props} key={option}>
      <Grid container alignItems="center">
        <Grid item>
          <Box>{displayName}</Box>
          <Typography variant="body2" color="text.secondary">
            {email}
          </Typography>
        </Grid>
      </Grid>
    </li>
  );
}

export function TableTopBarAutocomplete({
  defaultValue,
  filterOptions = (x) => x,
  ...restProps
}: Omit<AutocompleteProps<any, any, any, any>, 'renderInput'>) {
  return (
    <Autocomplete
      key={defaultValue}
      defaultValue={defaultValue}
      size="small"
      sx={{width: 320}}
      freeSolo
      filterOptions={filterOptions}
      renderInput={(params) => <TextField {...params} label="Search" autoFocus />}
      {...restProps}
    />
  );
}

interface TableTopBarProps {
  title: string;
  link?: string;
  children?: React.ReactNode;
}

export default function TableTopBar({title, link, children}: TableTopBarProps) {
  const navigate = useNavigate();
  return (
    <Stack
      direction="row"
      paddingTop={2}
      paddingBottom={1}
      paddingX={2}
      gap={4}
      justifyContent="space-between"
      flexWrap="wrap"
      alignItems="flex-end">
      <Stack direction="row" gap={1} alignItems="center">
        <Typography component="h5" variant="h5" color="text.accent">
          {title}
        </Typography>
        {link != null && (
          <IconButton size="small" onClick={() => navigate(link)}>
            <Launch />
          </IconButton>
        )}
      </Stack>
      <Stack direction="row" flexWrap="wrap" gap={1} alignItems="center">
        {children}
      </Stack>
    </Stack>
  );
}
