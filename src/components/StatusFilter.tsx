import React from 'react';
import Select, {SelectChangeEvent} from '@mui/material/Select';
import MenuItem from '@mui/material/MenuItem';
import FormControl from '@mui/material/FormControl';
import InputLabel from '@mui/material/InputLabel';

export type StatusFilterValue = 'PENDING' | 'APPROVED' | 'REJECTED' | 'ALL';

interface StatusFilterProps {
  value: StatusFilterValue;
  onChange: (event: SelectChangeEvent<StatusFilterValue>) => void;
}

export default function StatusFilter({value, onChange}: StatusFilterProps) {
  return (
    <FormControl size="small" sx={{minWidth: 120}}>
      <InputLabel id="status-filter-label">Status</InputLabel>
      <Select labelId="status-filter-label" value={value} label="Status" onChange={onChange}>
        <MenuItem value="ALL">All</MenuItem>
        <MenuItem value="PENDING">Pending</MenuItem>
        <MenuItem value="APPROVED">Approved</MenuItem>
        <MenuItem value="REJECTED">Rejected</MenuItem>
      </Select>
    </FormControl>
  );
}
