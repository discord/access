import {describe, it, expect} from 'vitest';
import {render, screen, within} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';

import RequestReviewers from './RequestReviewers';
import {ACCESS_APP_RESERVED_NAME} from '../authorization';
import {OktaUserSummary, RequestReviewers as RequestReviewersData} from '../api/apiSchemas';

const user = (id: string, first: string): OktaUserSummary => ({
  id,
  email: `${first.toLowerCase()}@example.com`,
  first_name: first,
  last_name: 'Tester',
  display_name: null,
});

const LEVELS: RequestReviewersData['owner_levels'] = [
  {owner_level: 'group_owners', reviewers: [user('u2', 'Zed'), user('u1', 'Amy')]},
  {owner_level: 'app_owners', reviewers: []},
  {owner_level: 'access_admins', reviewers: [user('u3', 'Root')]},
];

const renderPanel = (reviewers: RequestReviewersData | undefined) =>
  render(
    <MemoryRouter>
      <RequestReviewers reviewers={reviewers} groupName="App-Foo-Admin" appName="Foo" />
    </MemoryRouter>,
  );

describe('RequestReviewers', () => {
  it('renders nothing while the reviewers are loading', () => {
    const {container} = renderPanel(undefined);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders one titled table per owner level, in order', () => {
    renderPanel({assigned_owner_level: 'group_owners', owner_levels: LEVELS});
    const tables = screen.getAllByRole('table');
    expect(tables.map((t) => t.getAttribute('aria-label'))).toEqual(['group_owners', 'app_owners', 'access_admins']);
    expect(within(tables[0]).getByText('App-Foo-Admin Owners')).toBeTruthy();
    expect(within(tables[1]).getByText('Foo App Owners')).toBeTruthy();
    expect(within(tables[2]).getByText(`${ACCESS_APP_RESERVED_NAME} Admins`)).toBeTruthy();
  });

  it('marks only the assigned level and sorts reviewers by name', () => {
    renderPanel({assigned_owner_level: 'access_admins', owner_levels: LEVELS});
    const tables = screen.getAllByRole('table');
    expect(within(tables[0]).queryByText('Assigned')).toBeNull();
    expect(within(tables[1]).queryByText('Assigned')).toBeNull();
    expect(within(tables[2]).getByText('Assigned')).toBeTruthy();
    const names = within(tables[0])
      .getAllByRole('link')
      .filter((link) => link.textContent?.includes('Tester'))
      .map((link) => link.textContent);
    expect(names).toEqual(['Amy Tester', 'Zed Tester']);
  });

  it('warns when no level has an eligible reviewer', () => {
    renderPanel({assigned_owner_level: null, owner_levels: LEVELS});
    expect(screen.getByText('No one is currently eligible to review this request.')).toBeTruthy();
    expect(screen.queryByText('Assigned')).toBeNull();
  });

  it('does not warn when a level is assigned', () => {
    renderPanel({assigned_owner_level: 'group_owners', owner_levels: LEVELS});
    expect(screen.queryByText('No one is currently eligible to review this request.')).toBeNull();
  });

  it('keeps an empty level as a None row', () => {
    renderPanel({assigned_owner_level: 'group_owners', owner_levels: LEVELS});
    const appOwners = screen.getByRole('table', {name: 'app_owners'});
    expect(within(appOwners).getByText('None')).toBeTruthy();
  });
});
