import React from 'react';

/**
 * State for a list page's search box, seeded from the `q` URL search param.
 *
 * The box is filled from `q` only while it is empty, so a search the user is
 * still typing is never overwritten when another param (page, size, a filter)
 * changes the URL. The seeding effect re-runs only when `searchParams` changes.
 *
 * @param searchParams the page's search params, from `useSearchParams()`
 * @returns the `[searchInput, setSearchInput]` state pair
 */
export function useSearchInput(searchParams: URLSearchParams) {
  const [searchInput, setSearchInput] = React.useState('');

  React.useEffect(() => {
    // The functional update reads the current input without making it a
    // dependency, which would re-seed the box every time the user clears it.
    setSearchInput((prev) => (prev == '' ? (searchParams.get('q') ?? '') : prev));
  }, [searchParams]);

  return [searchInput, setSearchInput] as const;
}
