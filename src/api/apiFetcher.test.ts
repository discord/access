import {describe, it, expect, beforeEach, afterEach, vi} from 'vitest';
import {apiFetch} from './apiFetcher';

const assign = vi.fn();

const setLocation = (pathname: string, search = '', hash = '') => {
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: {pathname, search, hash, assign},
  });
};

const respondWith = (status: number, body: unknown) => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify(body), {status, headers: {'Content-Type': 'application/json'}})),
  );
};

// `apiFetch` deliberately never settles once it starts a login navigation, so
// race it against a timer rather than awaiting it.
const settles = (promise: Promise<unknown>) =>
  Promise.race([promise.then(() => true).catch(() => true), new Promise((r) => setTimeout(() => r(false), 20))]);

describe('apiFetch on 401', () => {
  beforeEach(() => {
    assign.mockClear();
    setLocation('/groups/acme-painter');
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('sends the browser to the login endpoint from the problem body', async () => {
    respondWith(401, {status: 401, detail: 'Authentication required', login_url: '/oidc/login'});
    const pending = apiFetch({url: '/api/users/@me', method: 'get'});
    expect(await settles(pending)).toBe(false);
    expect(assign).toHaveBeenCalledWith('/oidc/login?next=%2Fgroups%2Facme-painter');
  });

  it('asks to return to the current page, query string and fragment included', async () => {
    setLocation('/roles', '?q=painter&page=2', '#members');
    respondWith(401, {status: 401, detail: 'Authentication required', login_url: '/oidc/login'});
    expect(await settles(apiFetch({url: '/api/roles', method: 'get'}))).toBe(false);
    expect(assign).toHaveBeenCalledWith('/oidc/login?next=%2Froles%3Fq%3Dpainter%26page%3D2%23members');
  });

  it('falls back to the default login path when the body omits one', async () => {
    respondWith(401, {status: 401, detail: 'Authentication required'});
    expect(await settles(apiFetch({url: '/api/users/@me', method: 'get'}))).toBe(false);
    expect(assign).toHaveBeenCalledWith('/oidc/login?next=%2Fgroups%2Facme-painter');
  });

  it('leaves other error statuses to the caller', async () => {
    respondWith(403, {status: 403, detail: 'Forbidden'});
    await expect(apiFetch({url: '/api/users/@me', method: 'get'})).rejects.toMatchObject({payload: 'Forbidden'});
    expect(assign).not.toHaveBeenCalled();
  });
});
