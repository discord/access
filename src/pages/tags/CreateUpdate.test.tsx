import {render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {describe, expect, it, vi} from 'vitest';

// The dialog calls both mutation hooks at render. Neither is exercised here --
// these tests are about what the form says before anything is submitted.
vi.mock('../../api/apiComponents', () => ({
  useTagsCreate: () => ({mutate: vi.fn()}),
  useTagByIdPut: () => ({mutate: vi.fn()}),
}));

import CreateUpdateTag from './CreateUpdate';
import {OktaUserDetail, TagDetail} from '../../api/apiSchemas';
import {appName} from '../../config/accessConfig';
import {constraintLabel} from '../../constraintCopy';
import {DORMANT_UNTIL_ENABLED} from './tagChanges';

// Only an Access admin may open the dialog at all: a member of the owner group
// for the Access app itself.
const admin = {
  id: 'u1',
  email: 'admin@example.com',
  active_group_memberships: [
    {
      active_group: {
        id: 'g-owners',
        name: `App-${appName}-Owners`,
        type: 'app_group',
        is_owner: true,
        app: {id: 'a1', name: appName},
      },
    },
  ],
} as unknown as OktaUserDetail;

function tagFixture(overrides: Partial<TagDetail> = {}): TagDetail {
  return {
    id: 't1',
    name: 'SOX',
    description: '',
    enabled: true,
    propagate_to_roles: true,
    constraints: {},
    created_at: null,
    updated_at: null,
    // A tag applied to nothing has no blast radius, so the warning needs one.
    active_group_tags: [{id: 'gt1'}],
    ...overrides,
  } as TagDetail;
}

async function openDialog(tag: TagDetail) {
  render(
    <MemoryRouter>
      <CreateUpdateTag currentUser={admin} tag={tag} />
    </MemoryRouter>,
  );
  await userEvent.click(screen.getByRole('button', {name: 'edit'}));
}

describe('the constraint matrix', () => {
  // The row label ("Require a reason") and the column header ("Membership") name
  // the setting between them, but they sit in sibling grid cells that name
  // nothing to a screen reader -- leaving four checkboxes all announcing "Yes".
  it('names each checkbox by the constraint it sets, not just "Yes"', async () => {
    await openDialog(tagFixture());
    for (const key of [
      'require_member_reason',
      'require_owner_reason',
      'disallow_self_add_membership',
      'disallow_self_add_ownership',
    ]) {
      expect(screen.getByRole('checkbox', {name: constraintLabel(key)})).toBeInTheDocument();
    }
  });

  it('still shows "Yes" as the visible label', async () => {
    await openDialog(tagFixture());
    expect(screen.getAllByText('Yes')).toHaveLength(4);
  });
});

describe('the blast-radius warning', () => {
  const memberReason = () => screen.getByRole('checkbox', {name: constraintLabel('require_member_reason')});
  const warning = () => screen.queryByText(/^This tightens a tag applied to/);

  it('says nothing about a tag nobody has changed', async () => {
    await openDialog(tagFixture());
    expect(warning()).not.toBeInTheDocument();
  });

  it('warns when a constraint is newly imposed', async () => {
    await openDialog(tagFixture());
    await userEvent.click(memberReason());
    expect(warning()).toBeInTheDocument();
    expect(screen.getByText('A reason becomes required to grant membership.')).toBeInTheDocument();
  });

  // Enabling a tag applies every constraint it already stored to access that
  // already exists. Comparing the constraints alone misses it entirely, because
  // the edit need not touch a single one of them.
  it('warns when a disabled tag carrying a time limit is enabled', async () => {
    await openDialog(tagFixture({enabled: false, constraints: {member_time_limit: 604800}}));
    expect(warning()).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('radio', {name: 'Enabled'}));
    expect(warning()).toBeInTheDocument();
    expect(screen.getByText(/Membership longer than 7 days is shortened/)).toBeInTheDocument();
  });

  it('says nothing when enabling a tag that carries no constraints', async () => {
    await openDialog(tagFixture({enabled: false, constraints: {}}));
    await userEvent.click(screen.getByRole('radio', {name: 'Enabled'}));
    expect(warning()).not.toBeInTheDocument();
  });

  describe('a tag that will not be enabled on save', () => {
    it('still lists the effects, so the admin sees what they are committing', async () => {
      await openDialog(tagFixture());
      await userEvent.click(memberReason());
      await userEvent.click(screen.getByRole('radio', {name: 'Disabled'}));
      expect(warning()).toBeInTheDocument();
      expect(screen.getByText('A reason becomes required to grant membership.')).toBeInTheDocument();
    });

    it('says the effects wait on the tag being enabled', async () => {
      await openDialog(tagFixture());
      await userEvent.click(memberReason());
      expect(screen.queryByText(DORMANT_UNTIL_ENABLED)).not.toBeInTheDocument();

      await userEvent.click(screen.getByRole('radio', {name: 'Disabled'}));
      expect(screen.getByText(DORMANT_UNTIL_ENABLED)).toBeInTheDocument();
    });
  });
});
