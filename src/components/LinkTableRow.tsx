import React from 'react';
import {Link, TableRow} from '@mui/material';
import {Link as RouterLink, useNavigate} from 'react-router-dom';

interface LinkTableRowProps {
  to: string;
  children: React.ReactNode;
  onClick?: () => void;
}

export default function LinkTableRow({to, children}: LinkTableRowProps) {
  return (
    <TableRow>
      <Link
        component={RouterLink}
        to={to}
        sx={{
          display: 'contents',
          textDecoration: 'none',
          color: 'inherit',
          cursor: 'pointer',
          transition: 'all 0.2s ease',
          '&:hover td': {
            backgroundColor: (theme) => theme.palette.action.hover,
          },
        }}>
        {children}
      </Link>
    </TableRow>
  );
}
