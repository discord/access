import React from 'react';
import Chip from '@mui/material/Chip';
import {useNavigate} from 'react-router-dom';
import {RoleGroupMapDetail} from '../api/apiSchemas';

interface AccessMethodChipProps {
  // Only the role's name is read, so any mapping shape that carries it will do.
  roleGroupMapping?: Pick<RoleGroupMapDetail, 'role_group'> | null;
}

export default function AccessMethodChip({roleGroupMapping}: AccessMethodChipProps) {
  const navigate = useNavigate();

  if (!roleGroupMapping) {
    return <Chip label="Direct" color="primary" size="small" />;
  }

  return (
    <Chip
      label={roleGroupMapping.role_group?.name}
      variant="outlined"
      color="primary"
      size="small"
      onClick={() => navigate(`/roles/${roleGroupMapping.role_group?.name}`)}
      sx={{cursor: 'pointer'}}
    />
  );
}
