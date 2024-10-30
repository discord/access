import {Launch} from '@mui/icons-material';
import {IconButton, Link, Stack, Typography} from '@mui/material';

import * as React from 'react';
import {useNavigate} from 'react-router-dom';

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
      <Stack direction="row" gap={1} alignItems="center">
        {children}
      </Stack>
    </Stack>
  );
}
