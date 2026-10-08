import {describe, it, expect, vi, beforeEach, afterEach} from 'vitest';
import {render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {QueryClient, QueryClientProvider} from '@tanstack/react-query';

import {AppDetail, OktaUserDetail} from '../../api/apiSchemas';
import CreateUpdateGroup from './CreateUpdate';

vi.mock('react-router-dom', () => ({useNavigate: () => vi.fn()}));

const ACCESS_OWNER_GROUP = {
  id: 'access-owners-000000',
  type: 'app_group',
  name: 'App-Access-Owners',
  is_owner: true,
  app: {id: 'access-app-0000000000', name: 'Access'},
};

// The Access admin tier is membership in the Access app's owner group.
const ACCESS_ADMIN = {
  id: 'admin-00000000000000',
  email: 'admin@example.com',
  active_group_memberships: [{active_group: ACCESS_OWNER_GROUP}],
  active_group_ownerships: [],
} as unknown as OktaUserDetail;

const APP_NAME = 'HammerAndChiselZendeskSandbox';

// The app the dialog is opened from, as the app page holds it: an AppDetail, a
// different object from the summary GET /api/apps returns for the same app.
const APP = {
  id: 'zendesk-sandbox-0000',
  name: APP_NAME,
  description: 'Zendesk sandbox',
  plugin_data: {},
  active_app_tags: [],
} as unknown as AppDetail;

const APP_SUMMARY = {id: APP.id, name: APP.name, description: APP.description};

/** Apps that crowd out the target app on the first page of an empty search. */
const OTHER_APPS = [
  'Airtable',
  'Asana',
  'Datadog',
  'Figma',
  'Github',
  'Jira',
  'Linear',
  'Notion',
  'Sentry',
  'Slack',
].map((name, i) => ({id: `other-app-${i}`, name, description: name}));

let appQueries: string[] = [];

/**
 * Serves GET /api/apps from `page`, answering on a later tick and honouring the
 * abort signal. Both matter: a real round-trip leaves the query with no data
 * while it is in flight, and React Query cancels a request whose key is
 * superseded, so a cancelled key never populates the cache.
 */
const stubApps = (page: (q: string) => unknown[]) => {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: string, init?: RequestInit) => {
      const url = new URL(input, 'http://localhost');
      const q = url.searchParams.get('q') ?? '';
      const items = url.pathname === '/api/apps' ? (appQueries.push(q), page(q)) : [];
      const body = JSON.stringify({items, total: items.length, page: 1, size: 10, pages: 1});

      return new Promise<Response>((resolve, reject) => {
        const timer = setTimeout(
          () => resolve(new Response(body, {headers: {'Content-Type': 'application/json'}})),
          10,
        );
        init?.signal?.addEventListener('abort', () => {
          clearTimeout(timer);
          reject(Object.assign(new Error('aborted'), {name: 'AbortError'}));
        });
      });
    }),
  );
};

const searchByName = (q: string) =>
  [APP_SUMMARY, ...OTHER_APPS].filter((app) => app.name.toLowerCase().includes(q.toLowerCase()));

beforeEach(() => {
  appQueries = [];
  // The runaway needs the app present for some searches and absent for others,
  // which is what an ordinary search does as the query changes.
  stubApps(searchByName);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const openDialog = async () => {
  const queryClient = new QueryClient({defaultOptions: {queries: {retry: false}}});
  render(
    <QueryClientProvider client={queryClient}>
      <CreateUpdateGroup currentUser={ACCESS_ADMIN} defaultGroupType="app_group" app={APP} />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByRole('button', {name: 'Create App Group'}));
  return screen.getByRole('combobox', {name: /App/});
};

describe('the App field of the Create App Group dialog', () => {
  // The field's input feeds the /api/apps search that supplies its own options, so
  // anything that re-derives the displayed value from those options makes the two
  // chase each other: the search in flight has no options yet, so the value reads as
  // empty, which resets the input, which starts another search. That spun thousands
  // of requests a second and exhausted the backend's ports.
  it('settles after a bounded number of app searches', async () => {
    await openDialog();

    // Give any feedback loop room to run away before counting.
    await new Promise((resolve) => setTimeout(resolve, 300));

    expect(appQueries.length).toBeLessThanOrEqual(2);
  });

  // The dialog is opened from an app page, so the app is already known and the field
  // is locked to it. What the field shows has to come from the form value, not from
  // whichever apps the current search happens to return -- an app past the first page
  // of results is absent from every search the field runs on its own, and the user is
  // left staring at an empty required "App" dropdown.
  it('shows the app it is locked to even when the search does not return it', async () => {
    stubApps(() => OTHER_APPS);

    const appField = await openDialog();

    await waitFor(() => expect(appField).toHaveValue(APP_NAME));
  });
});
