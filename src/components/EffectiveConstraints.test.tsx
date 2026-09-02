import {render, screen} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import {describe, expect, it, vi} from 'vitest';

// This repo's MUI style engine is @mui/styled-engine-sc (styled-components),
// aliased in vite.config.ts for the app build. That alias doesn't reach
// vitest's dependency resolution: @mui/material's CJS build still requires
// the default `@mui/styled-engine`, which in turn requires `@emotion/styled`
// — a package that isn't installed here — so importing any real MUI
// component blows up at module-load time (see the identical workaround in
// AppGroupLifecyclePluginConfigurationForm.test.tsx). Stand in plain DOM
// elements that preserve the structure this component relies on (table
// semantics, text nodes, and forwarding Link's `component`/`to` props to the
// real react-router Link) so the assertions below exercise real rendered
// output rather than implementation details.
vi.mock('@mui/material/Accordion', () => ({default: ({children}: any) => <div>{children}</div>}));
vi.mock('@mui/material/AccordionSummary', () => ({default: ({children}: any) => <div>{children}</div>}));
vi.mock('@mui/material/AccordionDetails', () => ({default: ({children}: any) => <div>{children}</div>}));
vi.mock('@mui/material/Paper', () => ({default: ({children}: any) => <div>{children}</div>}));
vi.mock('@mui/material/TableContainer', () => ({default: ({children}: any) => <div>{children}</div>}));
vi.mock('@mui/material/Table', () => ({default: ({children}: any) => <table>{children}</table>}));
vi.mock('@mui/material/TableHead', () => ({default: ({children}: any) => <thead>{children}</thead>}));
vi.mock('@mui/material/TableBody', () => ({default: ({children}: any) => <tbody>{children}</tbody>}));
vi.mock('@mui/material/TableRow', () => ({default: ({children}: any) => <tr>{children}</tr>}));
vi.mock('@mui/material/TableCell', () => ({default: ({children}: any) => <td>{children}</td>}));
vi.mock('@mui/material/Typography', () => ({default: ({children}: any) => <span>{children}</span>}));
vi.mock('@mui/material/Link', () => ({
  default: ({component: Component, children, ...rest}: any) => {
    const Comp = Component || 'a';
    return <Comp {...rest}>{children}</Comp>;
  },
}));
vi.mock('@mui/icons-material/ExpandMore', () => ({default: () => null}));

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

describe('EffectiveConstraints', () => {
  it('renders nothing when no constraints are in force', () => {
    const {container} = renderPanel([]);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the count in the summary', () => {
    renderPanel([timeLimit, flag]);
    expect(screen.getByText('Effective constraints (2)')).toBeInTheDocument();
  });

  it('renders a time limit in days, folded into the constraint column', () => {
    renderPanel([timeLimit]);
    expect(screen.getByText('Limit time of membership — 90 days')).toBeInTheDocument();
  });

  it('renders a flag without a value suffix', () => {
    renderPanel([flag]);
    expect(screen.getByText('Require reason for member access')).toBeInTheDocument();
  });

  it('links the tag and links the group the constraint reaches this one through', () => {
    renderPanel([timeLimit]);
    expect(screen.getByRole('link', {name: 'SOX'})).toHaveAttribute('href', '/tags/SOX');
    expect(screen.getByText(/via membership in/)).toBeInTheDocument();
    expect(screen.getByRole('link', {name: 'App-Foo-Admin'})).toHaveAttribute('href', '/groups/App-Foo-Admin');
  });

  it('links an owner-association source as a group as well', () => {
    renderPanel([
      {
        ...timeLimit,
        sources: [{...timeLimit.sources[0], origin: 'owner_association', source_name: 'Payments-Config'}],
      },
    ]);
    expect(screen.getByText(/via ownership of/)).toBeInTheDocument();
    expect(screen.getByRole('link', {name: 'Payments-Config'})).toHaveAttribute('href', '/groups/Payments-Config');
  });

  it('links an app-inherited source to the app, not to a group', () => {
    // The "source" of an app origin is an App, which lives at a different
    // route — hence the origin-agnostic field names on the API side.
    renderPanel([
      {...timeLimit, sources: [{...timeLimit.sources[0], origin: 'app', source_id: 'a1', source_name: 'Ledger'}]},
    ]);
    expect(screen.getByText(/via app/)).toBeInTheDocument();
    expect(screen.getByRole('link', {name: 'Ledger'})).toHaveAttribute('href', '/apps/Ledger');
  });

  it('states the origin without a link when the app behind it is gone', () => {
    // `active_app` filters soft-deleted apps, so an inherited tag can outlive
    // the app's record. Linking to a name we do not have would be a dead link.
    renderPanel([
      {...timeLimit, sources: [{...timeLimit.sources[0], origin: 'app', source_id: null, source_name: null}]},
    ]);
    expect(screen.getByText(/via app/)).toBeInTheDocument();
    expect(screen.queryAllByRole('link')).toHaveLength(1); // the tag only
  });

  it('gives a direct source no second link, having no other site to point at', () => {
    renderPanel([flag]);
    expect(screen.getByText(/direct/)).toBeInTheDocument();
    expect(screen.queryAllByRole('link')).toHaveLength(1);
  });

  it('rounds a time limit that does not divide evenly into days, and says "day" singular', () => {
    renderPanel([{...timeLimit, value: 90000}]); // 1.0416... days
    expect(screen.getByText('Limit time of membership — 1 day')).toBeInTheDocument();
  });

  it('renders a sub-day time limit as "<1 day" rather than rounding it to "0 days"', () => {
    // A one-hour limit is a legal constraint value (the validator only
    // requires a positive integer), and the propagation tests use exactly
    // this. Rounding it to the nearest day would claim no access at all.
    renderPanel([{...timeLimit, value: 3600}]);
    expect(screen.getByText('Limit time of membership — <1 day')).toBeInTheDocument();
  });

  it('renders an exactly-one-day limit as singular', () => {
    renderPanel([{...timeLimit, value: 86400}]);
    expect(screen.getByText('Limit time of membership — 1 day')).toBeInTheDocument();
  });

  it('renders an unrecognized origin as itself, not as a false "direct" claim', () => {
    renderPanel([
      {
        ...timeLimit,
        sources: [{tag_id: 't1', tag_name: 'SOX', origin: 'some_future_origin', source_id: null, source_name: null}],
      },
    ]);
    expect(screen.getByText(/some_future_origin/)).toBeInTheDocument();
  });

  it('does not crash when a source entry omits `sources` (an optional field per the API contract)', () => {
    const {container} = renderPanel([{constraint: 'require_member_reason', name: 'Require reason', value: true}]);
    expect(container).not.toBeEmptyDOMElement();
    expect(screen.getByText('Require reason')).toBeInTheDocument();
  });
});
