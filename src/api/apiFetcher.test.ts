import {describe, it, expect, vi, beforeEach, afterEach} from 'vitest';

import {apiFetch} from './apiFetcher';

// The URL the fetcher actually builds, which is the only place an array-valued
// query parameter is turned into a query string. `/api/constraints/effective`
// is the first endpoint to take one, so this is the first time the difference
// between "repeated key" and "comma-joined value" has been observable.

function stubFetch(status = 200, body: unknown = {}) {
  const spy = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
    Promise.resolve(new Response(JSON.stringify(body), {status, headers: {'Content-Type': 'application/json'}})),
  );
  vi.stubGlobal('fetch', spy);
  window.fetch = spy;
  return spy;
}

function calledUrl(spy: ReturnType<typeof stubFetch>): string {
  const url = spy.mock.calls[0][0];
  if (typeof url !== 'string') {
    throw new Error('expected a string URL');
  }
  return url;
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

describe('apiFetch on 401', () => {
  const assign = vi.fn();
  const realLocation = window.location;

  const setLocation = (pathname: string, search = '', hash = '') => {
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: {pathname, search, hash, assign},
    });
  };

  // `apiFetch` deliberately never settles once it has handed the window to the
  // login endpoint, so race it against a timer rather than awaiting it.
  const settles = (promise: Promise<unknown>): Promise<boolean> =>
    Promise.race([
      promise.then(
        () => true,
        () => true,
      ),
      new Promise<boolean>((resolve) => setTimeout(() => resolve(false), 20)),
    ]);

  beforeEach(() => {
    assign.mockClear();
    setLocation('/groups/acme-painter');
  });

  afterEach(() => {
    Object.defineProperty(window, 'location', {configurable: true, value: realLocation});
    vi.unstubAllGlobals();
  });

  it('sends the browser to the login endpoint named in the problem body', async () => {
    stubFetch(401, {status: 401, detail: 'Authentication required', login_url: '/oidc/login'});

    expect(await settles(apiFetch({url: '/api/users/@me', method: 'get'}))).toBe(false);
    expect(assign).toHaveBeenCalledWith('/oidc/login?next=%2Fgroups%2Facme-painter');
  });

  it('asks to return to the current page, query string and fragment included', async () => {
    setLocation('/roles', '?q=painter&page=2', '#members');
    stubFetch(401, {status: 401, detail: 'Authentication required', login_url: '/oidc/login'});

    expect(await settles(apiFetch({url: '/api/roles', method: 'get'}))).toBe(false);
    expect(assign).toHaveBeenCalledWith('/oidc/login?next=%2Froles%3Fq%3Dpainter%26page%3D2%23members');
  });

  it('falls back to the default login path when the body omits one', async () => {
    stubFetch(401, {status: 401, detail: 'Authentication required'});

    expect(await settles(apiFetch({url: '/api/users/@me', method: 'get'}))).toBe(false);
    expect(assign).toHaveBeenCalledWith('/oidc/login?next=%2Fgroups%2Facme-painter');
  });

  it('leaves other error statuses to the caller', async () => {
    stubFetch(403, {status: 403, detail: 'Forbidden'});

    await expect(apiFetch({url: '/api/users/@me', method: 'get'})).rejects.toMatchObject({payload: 'Forbidden'});
    expect(assign).not.toHaveBeenCalled();
  });
});
