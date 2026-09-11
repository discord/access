import {describe, it, expect, vi, beforeEach, afterEach} from 'vitest';

import {apiFetch} from './apiFetcher';

// The URL the fetcher actually builds, which is the only place an array-valued
// query parameter is turned into a query string. `/api/constraints/effective`
// is the first endpoint to take one, so this is the first time the difference
// between "repeated key" and "comma-joined value" has been observable.

function stubFetch() {
  const spy = vi.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response('{}', {status: 200, headers: {'Content-Type': 'application/json'}}),
  );
  vi.stubGlobal('fetch', spy);
  window.fetch = spy as unknown as typeof window.fetch;
  return spy;
}

function calledUrl(spy: ReturnType<typeof stubFetch>): string {
  return String(spy.mock.calls[0][0]);
}

describe('apiFetch query parameters', () => {
  let spy: ReturnType<typeof stubFetch>;

  beforeEach(() => {
    spy = stubFetch();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('sends an array as one repeated key', async () => {
    await apiFetch({
      url: '/api/constraints/effective',
      method: 'get',
      queryParams: {group_ids: ['g1', 'g2', 'g3']},
    });

    const query = calledUrl(spy).split('?')[1];
    // Not `group_ids=g1%2Cg2%2Cg3`: FastAPI reads that as a single id literally
    // named "g1,g2,g3", matches no group, and answers with an empty roll-up
    // that a caller cannot tell apart from "nothing is constrained".
    expect(query).toBe('group_ids=g1&group_ids=g2&group_ids=g3');
  });

  it('sends a single-element array the same way', async () => {
    // This one case survived comma-joining, which is what let the single-group
    // dialogs look correct while every bulk one was broken.
    await apiFetch({url: '/api/constraints/effective', method: 'get', queryParams: {group_ids: ['only']}});

    expect(calledUrl(spy).split('?')[1]).toBe('group_ids=only');
  });

  it('leaves scalar parameters alone', async () => {
    await apiFetch({url: '/api/groups', method: 'get', queryParams: {page: 2, size: 50, q: 'a b'}});

    expect(calledUrl(spy).split('?')[1]).toBe('page=2&size=50&q=a+b');
  });

  it('omits an empty array rather than sending an empty value', async () => {
    await apiFetch({url: '/api/constraints/effective', method: 'get', queryParams: {group_ids: [], tag_ids: ['t1']}});

    expect(calledUrl(spy).split('?')[1]).toBe('tag_ids=t1');
  });

  it('still substitutes path parameters', async () => {
    await apiFetch({
      url: '/api/groups/{groupId}/member-details',
      method: 'get',
      pathParams: {groupId: 'grp-1'},
      queryParams: {owner: true},
    });

    expect(calledUrl(spy)).toContain('/api/groups/grp-1/member-details?owner=true');
  });
});
