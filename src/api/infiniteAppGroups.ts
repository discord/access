import {useInfiniteQuery} from '@tanstack/react-query';

import {fetchAppGroupsById} from './apiComponents';
import {useApiContext} from './apiContext';
import {deepMerge} from './apiUtils';

/**
 * Infinite-scroll variant of `useAppGroupsById`: pages an app's groups
 * (owners-first, 10/page) and accumulates them across pages so the app page can
 * load more on scroll instead of via a page-number control. `owner` filters
 * owner vs non-owner groups; `q` is the server-side member search (omitted when
 * empty so the fetcher doesn't serialize it as the literal "undefined"). Reuses
 * the generated fetcher + API context so auth options are injected like the
 * generated hooks.
 */
export function useInfiniteAppGroups(appId: string, owner: boolean, q = '', enabled = true) {
  const {fetcherOptions} = useApiContext();
  return useInfiniteQuery({
    queryKey: ['appGroups', appId, {owner, q}],
    initialPageParam: 1,
    queryFn: ({pageParam, signal}) =>
      fetchAppGroupsById(
        deepMerge(fetcherOptions, {
          pathParams: {appId},
          queryParams: Object.assign({owner, page: pageParam as number}, q ? {q} : null),
        }),
        signal,
      ),
    getNextPageParam: (lastPage) => (lastPage.page < lastPage.pages ? lastPage.page + 1 : undefined),
    enabled: enabled && !!appId,
  });
}
