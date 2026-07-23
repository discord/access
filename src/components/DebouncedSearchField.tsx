import Autocomplete from '@mui/material/Autocomplete';
import TextField from '@mui/material/TextField';
import {SxProps, Theme} from '@mui/material/styles';
import React from 'react';

// How long to wait after the last keystroke before firing the search query.
const SEARCH_DEBOUNCE_MS = 200;

interface DebouncedSearchFieldProps {
  label: string;
  // Emits the trimmed query, debounced, once the user stops typing — so callers
  // filter server-side without a request per keystroke.
  onSearchChange: (q: string) => void;
  debounceMs?: number;
  sx?: SxProps<Theme>;
  autoFocus?: boolean;
}

// Search-as-you-type field: a free-solo Autocomplete (no suggestion list) that
// debounces input and emits the trimmed query. Used wherever a list is paged
// server-side and can't be filtered client-side (app groups, group members).
export default function DebouncedSearchField({
  label,
  onSearchChange,
  debounceMs = SEARCH_DEBOUNCE_MS,
  sx,
  autoFocus,
}: DebouncedSearchFieldProps) {
  const onSearchChangeRef = React.useRef(onSearchChange);
  onSearchChangeRef.current = onSearchChange;

  const debounceRef = React.useRef<ReturnType<typeof setTimeout>>();
  const handleInputChange = React.useCallback(
    (_: unknown, newValue: string | null) => {
      const q = newValue?.trim() ?? '';
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }
      debounceRef.current = setTimeout(() => onSearchChangeRef.current?.(q), debounceMs);
    },
    [debounceMs],
  );

  React.useEffect(
    () => () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }
    },
    [],
  );

  return (
    <Autocomplete
      size="small"
      sx={sx}
      renderInput={(params) => <TextField {...params} label={label} />}
      options={[]}
      onInputChange={handleInputChange}
      clearOnEscape
      freeSolo
      autoFocus={autoFocus}
    />
  );
}
