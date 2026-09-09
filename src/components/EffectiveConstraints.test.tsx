import {render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {describe, expect, it} from 'vitest';

import EffectiveConstraints from './EffectiveConstraints';

const timeLimit = {
  constraint: 'member_time_limit',
  name: 'Limit time of membership',
  value: 7776000,
  sources: [
    {
      tag_id: 't1',
      tag_name: 'SOX',
      origin: 'member_association',
      source_id: 'g1',
      source_name: 'App-Foo-Admin',
    },
  ],
};

const flag = {
  constraint: 'require_member_reason',
  name: 'Require reason for member access',
  value: true,
  sources: [{tag_id: 't1', tag_name: 'SOX', origin: 'direct', source_id: null, source_name: null}],
};

const renderPanel = (constraints: any[]) =>
  render(
    <MemoryRouter>
      <EffectiveConstraints constraints={constraints} />
    </MemoryRouter>,
  );

// The panel starts collapsed, and a collapsed accordion keeps its contents out
// of the accessibility tree. Anything asserted about a row has to be revealed
// the way a reader reveals it.
const openPanel = async (constraints: any[]) => {
  const result = renderPanel(constraints);
  await userEvent.click(screen.getByRole('button', {name: /Effective constraints/}));
  return result;
};

describe('EffectiveConstraints', () => {
  it('renders nothing when no constraints are in force', () => {
    const {container} = renderPanel([]);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the count in the summary', () => {
    renderPanel([timeLimit, flag]);
    expect(screen.getByText('Effective constraints (2)')).toBeInTheDocument();
  });

  it('renders a time limit in days, folded into the constraint column', async () => {
    await openPanel([timeLimit]);
    expect(screen.getByText('Limit time of membership — 90 days')).toBeInTheDocument();
  });

  it('renders a flag without a value suffix', async () => {
    await openPanel([flag]);
    expect(screen.getByText('Require reason for member access')).toBeInTheDocument();
  });

  it('links the tag and links the group the constraint reaches this one through', async () => {
    await openPanel([timeLimit]);
    expect(screen.getByRole('link', {name: 'SOX'})).toHaveAttribute('href', '/tags/SOX');
    expect(screen.getByText(/via membership in/)).toBeInTheDocument();
    expect(screen.getByRole('link', {name: 'App-Foo-Admin'})).toHaveAttribute('href', '/groups/App-Foo-Admin');
  });

  it('links an owner-association source as a group as well', async () => {
    await openPanel([
      {
        ...timeLimit,
        sources: [{...timeLimit.sources[0], origin: 'owner_association', source_name: 'Payments-Config'}],
      },
    ]);
    expect(screen.getByText(/via ownership of/)).toBeInTheDocument();
    expect(screen.getByRole('link', {name: 'Payments-Config'})).toHaveAttribute('href', '/groups/Payments-Config');
  });

  it('links an app-inherited source to the app, not to a group', async () => {
    // The "source" of an app origin is an App, which lives at a different
    // route — hence the origin-agnostic field names on the API side.
    await openPanel([
      {...timeLimit, sources: [{...timeLimit.sources[0], origin: 'app', source_id: 'a1', source_name: 'Ledger'}]},
    ]);
    expect(screen.getByText(/via app/)).toBeInTheDocument();
    expect(screen.getByRole('link', {name: 'Ledger'})).toHaveAttribute('href', '/apps/Ledger');
  });

  it('states the origin without a link when the app behind it is gone', async () => {
    // `active_app` filters soft-deleted apps, so an inherited tag can outlive
    // the app's record. Linking to a name we do not have would be a dead link.
    await openPanel([
      {...timeLimit, sources: [{...timeLimit.sources[0], origin: 'app', source_id: null, source_name: null}]},
    ]);
    expect(screen.getByText(/via app/)).toBeInTheDocument();
    expect(screen.queryAllByRole('link')).toHaveLength(1); // the tag only
  });

  it('gives a direct source no second link, having no other site to point at', async () => {
    await openPanel([flag]);
    expect(screen.getByText(/direct/)).toBeInTheDocument();
    expect(screen.queryAllByRole('link')).toHaveLength(1);
  });

  it('rounds a time limit that does not divide evenly into days, and says "day" singular', async () => {
    await openPanel([{...timeLimit, value: 90000}]); // 1.0416... days
    expect(screen.getByText('Limit time of membership — 1 day')).toBeInTheDocument();
  });

  it('renders a sub-day time limit as "<1 day" rather than rounding it to "0 days"', async () => {
    // A one-hour limit is a legal constraint value (the validator only
    // requires a positive integer), and the propagation tests use exactly
    // this. Rounding it to the nearest day would claim no access at all.
    await openPanel([{...timeLimit, value: 3600}]);
    expect(screen.getByText('Limit time of membership — <1 day')).toBeInTheDocument();
  });

  it('renders an exactly-one-day limit as singular', async () => {
    await openPanel([{...timeLimit, value: 86400}]);
    expect(screen.getByText('Limit time of membership — 1 day')).toBeInTheDocument();
  });

  it('renders an unrecognized origin as itself, not as a false "direct" claim', async () => {
    await openPanel([
      {
        ...timeLimit,
        sources: [{tag_id: 't1', tag_name: 'SOX', origin: 'some_future_origin', source_id: null, source_name: null}],
      },
    ]);
    expect(screen.getByText(/some_future_origin/)).toBeInTheDocument();
  });

  it('does not crash when a source entry omits `sources` (an optional field per the API contract)', async () => {
    const {container} = await openPanel([{constraint: 'require_member_reason', name: 'Require reason', value: true}]);
    expect(container).not.toBeEmptyDOMElement();
    expect(screen.getByText('Require reason')).toBeInTheDocument();
  });
});
