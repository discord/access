import * as React from 'react';
import {useNavigate} from 'react-router-dom';
import {keepPreviousData} from '@tanstack/react-query';

import Box from '@mui/material/Box';
import CircularProgress from '@mui/material/CircularProgress';
import Dialog from '@mui/material/Dialog';
import Divider from '@mui/material/Divider';
import InputAdornment from '@mui/material/InputAdornment';
import List from '@mui/material/List';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import ListSubheader from '@mui/material/ListSubheader';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';

import SearchIcon from '@mui/icons-material/Search';
import UserIcon from '@mui/icons-material/Person';
import GroupIcon from '@mui/icons-material/People';
import RoleIcon from '@mui/icons-material/Diversity3';
import AppIcon from '@mui/icons-material/AppShortcut';

import {useApps, useGroups, useRoles, useUsers} from '../api/apiComponents';
import {AppSummary, GroupSummary, OktaUserSummary, RoleGroupListItem} from '../api/apiSchemas';
import {displayGroupType, displayUserName} from '../helpers';

// Number of results to surface per category. Kept small so the palette stays
// scannable — the full list pages are a click away for exhaustive browsing.
const RESULTS_PER_CATEGORY = 5;

// The API returns matches ordered alphabetically (by email/name), not by
// relevance, and its user search also matches hidden profile fields (e.g. a
// person's manager). So we over-fetch a candidate pool and re-rank client-side
// against the visible fields, then trim to RESULTS_PER_CATEGORY.
const CANDIDATE_POOL_SIZE = 25;

// How long to wait after the last keystroke before firing the search requests.
const SEARCH_DEBOUNCE_MS = 200;

interface SwitcherResult {
  key: string;
  category: string;
  primary: string;
  secondary?: string;
  to: string;
  icon: React.ReactNode;
  // Visible fields the query is scored against for relevance ranking.
  rankFields: Array<string | null | undefined>;
}

// Lower is better. 0 = exact match, 1 = prefix, 2 = substring, 3 = matched only
// via a field we don't display (e.g. a user's manager in their Okta profile).
function relevanceScore(query: string, fields: Array<string | null | undefined>): number {
  const q = query.toLowerCase();
  let best = 3;
  for (const raw of fields) {
    if (!raw) continue;
    const field = raw.toLowerCase();
    if (field === q) return 0;
    if (field.startsWith(q)) best = Math.min(best, 1);
    else if (field.includes(q)) best = Math.min(best, 2);
  }
  return best;
}

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const handle = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(handle);
  }, [value, delayMs]);
  return debounced;
}

interface QuickSwitcherProps {
  open: boolean;
  onClose: () => void;
}

export default function QuickSwitcher({open, onClose}: QuickSwitcherProps) {
  const navigate = useNavigate();

  const [query, setQuery] = React.useState('');
  const [highlightedIndex, setHighlightedIndex] = React.useState(0);

  const debouncedQuery = useDebouncedValue(query, SEARCH_DEBOUNCE_MS);
  const trimmedQuery = debouncedQuery.trim();
  const shouldSearch = open && trimmedQuery.length > 0;

  // Reset the input each time the palette is opened so it always starts fresh.
  React.useEffect(() => {
    if (open) {
      setQuery('');
      setHighlightedIndex(0);
    }
  }, [open]);

  // A brand new query should always start focused on the first result.
  React.useEffect(() => {
    setHighlightedIndex(0);
  }, [debouncedQuery]);

  const searchOptions = {enabled: shouldSearch, placeholderData: keepPreviousData};

  const {data: usersData, isFetching: usersFetching} = useUsers(
    {queryParams: {page: 1, size: CANDIDATE_POOL_SIZE, q: trimmedQuery}},
    searchOptions,
  );
  const {data: groupsData, isFetching: groupsFetching} = useGroups(
    {queryParams: {page: 1, size: CANDIDATE_POOL_SIZE, q: trimmedQuery}},
    searchOptions,
  );
  const {data: rolesData, isFetching: rolesFetching} = useRoles(
    {queryParams: {page: 1, size: CANDIDATE_POOL_SIZE, q: trimmedQuery}},
    searchOptions,
  );
  const {data: appsData, isFetching: appsFetching} = useApps(
    {queryParams: {page: 1, size: CANDIDATE_POOL_SIZE, q: trimmedQuery}},
    searchOptions,
  );

  const isFetching = usersFetching || groupsFetching || rolesFetching || appsFetching;

  const results = React.useMemo<SwitcherResult[]>(() => {
    if (!shouldSearch) {
      return [];
    }

    // Re-rank a category's candidates by relevance against the visible fields,
    // then keep only the top few. The API's sort is a stable secondary key, so
    // equally-relevant items stay in the (alphabetical) order it returned.
    const rankAndTrim = (candidates: SwitcherResult[]): SwitcherResult[] =>
      candidates
        .map((result, index) => ({result, index, score: relevanceScore(trimmedQuery, result.rankFields)}))
        .sort((a, b) => a.score - b.score || a.index - b.index)
        .slice(0, RESULTS_PER_CATEGORY)
        .map((entry) => entry.result);

    const users = rankAndTrim(
      (usersData?.items ?? []).map((user: OktaUserSummary) => {
        const email = user.email.toLowerCase();
        return {
          key: `user-${user.id}`,
          category: 'Users',
          primary: displayUserName(user),
          secondary: email,
          to: `/users/${email}`,
          icon: <UserIcon fontSize="small" />,
          rankFields: [displayUserName(user), email],
        };
      }),
    );

    // Role groups are surfaced under their own category, so keep them out of the
    // Groups list to avoid duplicate entries pointing at the same page.
    const groups = rankAndTrim(
      (groupsData?.items ?? [])
        .filter((group: GroupSummary) => group.type !== 'role_group')
        .map((group: GroupSummary) => ({
          key: `group-${group.id}`,
          category: 'Groups',
          primary: group.name,
          secondary: displayGroupType(group),
          to: `/groups/${group.name}`,
          icon: <GroupIcon fontSize="small" />,
          rankFields: [group.name],
        })),
    );

    const roles = rankAndTrim(
      (rolesData?.items ?? []).map((role: RoleGroupListItem) => ({
        key: `role-${role.id}`,
        category: 'Roles',
        primary: role.name,
        secondary: role.description ?? undefined,
        to: `/roles/${role.name}`,
        icon: <RoleIcon fontSize="small" />,
        rankFields: [role.name, role.description],
      })),
    );

    const apps = rankAndTrim(
      (appsData?.items ?? []).map((app: AppSummary) => ({
        key: `app-${app.id}`,
        category: 'Apps',
        primary: app.name,
        secondary: app.description ?? undefined,
        to: `/apps/${app.name}`,
        icon: <AppIcon fontSize="small" />,
        rankFields: [app.name, app.description],
      })),
    );

    return [...users, ...groups, ...roles, ...apps];
  }, [shouldSearch, trimmedQuery, usersData, groupsData, rolesData, appsData]);

  const goTo = React.useCallback(
    (result: SwitcherResult) => {
      onClose();
      navigate(result.to);
    },
    [navigate, onClose],
  );

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (results.length === 0) {
      return;
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setHighlightedIndex((index) => (index + 1) % results.length);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setHighlightedIndex((index) => (index - 1 + results.length) % results.length);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const result = results[Math.min(highlightedIndex, results.length - 1)];
      if (result) {
        goTo(result);
      }
    }
  };

  // Keep the highlighted row visible as the user arrows through the list.
  const selectedRef = React.useRef<HTMLDivElement | null>(null);
  React.useEffect(() => {
    selectedRef.current?.scrollIntoView({block: 'nearest'});
  }, [highlightedIndex]);

  // The dialog's focus trap can steal focus back after the open transition, so
  // `autoFocus` alone isn't reliable — focus the input once the dialog is shown.
  const inputRef = React.useRef<HTMLInputElement | null>(null);
  const focusInput = React.useCallback(() => inputRef.current?.focus(), []);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="sm"
      sx={{'& .MuiDialog-container': {alignItems: 'flex-start'}}}
      PaperProps={{sx: {mt: '12vh', borderRadius: 2}}}
      TransitionProps={{onEntered: focusInput}}>
      <Box onKeyDown={handleKeyDown}>
        <TextField
          autoFocus
          inputRef={inputRef}
          fullWidth
          variant="standard"
          placeholder="Search users, groups, roles, and apps…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          InputProps={{
            disableUnderline: true,
            sx: {fontSize: '1.1rem', px: 2, py: 1.5},
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon color="action" />
              </InputAdornment>
            ),
            endAdornment: isFetching ? (
              <InputAdornment position="end">
                <CircularProgress size={18} />
              </InputAdornment>
            ) : null,
          }}
        />
        <Divider />
        {trimmedQuery.length === 0 ? (
          <Box sx={{px: 3, py: 4, textAlign: 'center'}}>
            <Typography variant="body2" color="text.secondary">
              Start typing to search across users, groups, roles, and apps.
            </Typography>
          </Box>
        ) : results.length === 0 && !isFetching ? (
          <Box sx={{px: 3, py: 4, textAlign: 'center'}}>
            <Typography variant="body2" color="text.secondary">
              No results for “{trimmedQuery}”.
            </Typography>
          </Box>
        ) : (
          <List dense sx={{maxHeight: '50vh', overflowY: 'auto', py: 0}}>
            {results.map((result, index) => {
              const showHeader = index === 0 || results[index - 1].category !== result.category;
              return (
                <React.Fragment key={result.key}>
                  {showHeader && <ListSubheader disableSticky>{result.category}</ListSubheader>}
                  <ListItemButton
                    ref={index === highlightedIndex ? selectedRef : undefined}
                    selected={index === highlightedIndex}
                    onMouseMove={() => setHighlightedIndex(index)}
                    onClick={() => goTo(result)}>
                    <ListItemIcon sx={{minWidth: 36}}>{result.icon}</ListItemIcon>
                    <ListItemText
                      primary={result.primary}
                      secondary={result.secondary}
                      primaryTypographyProps={{noWrap: true}}
                      secondaryTypographyProps={{noWrap: true}}
                    />
                  </ListItemButton>
                </React.Fragment>
              );
            })}
          </List>
        )}
        <Divider />
        <Box sx={{display: 'flex', gap: 2, px: 2, py: 1}}>
          <Typography variant="caption" color="text.secondary">
            <kbd>↑</kbd> <kbd>↓</kbd> to navigate
          </Typography>
          <Typography variant="caption" color="text.secondary">
            <kbd>↵</kbd> to select
          </Typography>
          <Typography variant="caption" color="text.secondary">
            <kbd>esc</kbd> to close
          </Typography>
        </Box>
      </Box>
    </Dialog>
  );
}
