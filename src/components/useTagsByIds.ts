import * as React from 'react';
import {useQueries} from '@tanstack/react-query';

import {tagByIdQuery} from '../api/apiComponents';
import {TagDetail, TagListItem} from '../api/apiSchemas';

// Every tag named in `ids`, each fetched by its own id.
//
// Not by filtering a page of the tag list: a request can name a tag that no
// single page of that list contains, and a partial match would drop it from
// the approval -- and from the constraints answered for -- without saying so.
export function useTagsByIds(ids: string[]): {tags: TagListItem[]; settled: boolean} {
  const results = useQueries({queries: ids.map((id) => tagByIdQuery({pathParams: {tagId: id}}))});
  const signature = results.map((result) => `${result.status}:${result.dataUpdatedAt}`).join('|');
  return React.useMemo(
    () => ({
      // A tag that no longer exists answers with an error rather than data. It
      // cannot be applied either way, so it is left out of the list while
      // still counting as settled.
      tags: results.map((result) => result.data).filter((tag): tag is TagDetail => tag != null),
      settled: results.every((result) => result.data !== undefined || result.error != null),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [signature],
  );
}
