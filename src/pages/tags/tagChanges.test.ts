import {describe, expect, it} from 'vitest';

import {DORMANT_UNTIL_ENABLED, TagSettings, tighteningEffects} from './tagChanges';

const SETTINGS: TagSettings = {
  memberTimeLimitDays: 30,
  ownerTimeLimitDays: 30,
  requireMemberReason: false,
  requireOwnerReason: false,
  disallowSelfAddMembership: false,
  disallowSelfAddOwnership: false,
  propagatesToRoles: true,
  enabled: true,
};

const tag = (overrides: Partial<TagSettings> = {}): TagSettings => ({...SETTINGS, ...overrides});

describe('tighteningEffects', () => {
  it('says nothing when nothing changes', () => {
    expect(tighteningEffects(tag(), tag())).toEqual([]);
  });

  describe('loosening, which changes no access already granted', () => {
    it('says nothing when a time limit is raised', () => {
      expect(tighteningEffects(tag({memberTimeLimitDays: 7}), tag({memberTimeLimitDays: 30}))).toEqual([]);
    });

    it('says nothing when a time limit is removed altogether', () => {
      expect(tighteningEffects(tag({memberTimeLimitDays: 7}), tag({memberTimeLimitDays: undefined}))).toEqual([]);
    });

    it('says nothing when a reason requirement is cleared', () => {
      expect(tighteningEffects(tag({requireMemberReason: true}), tag({requireMemberReason: false}))).toEqual([]);
    });

    it('says nothing when a self-add restriction is cleared', () => {
      expect(tighteningEffects(tag({disallowSelfAddOwnership: true}), tag({disallowSelfAddOwnership: false}))).toEqual(
        [],
      );
    });

    it('says nothing when the scope narrows', () => {
      expect(tighteningEffects(tag({propagatesToRoles: true}), tag({propagatesToRoles: false}))).toEqual([]);
    });
  });

  describe('tightening', () => {
    it('warns when a time limit is lowered', () => {
      const [effect] = tighteningEffects(tag({memberTimeLimitDays: 30}), tag({memberTimeLimitDays: 7}));
      expect(effect).toContain('7 days');
      expect(effect).toContain('shortened');
    });

    it('warns when a limit is set where there was none, since every grant then exceeds it', () => {
      const [effect] = tighteningEffects(tag({ownerTimeLimitDays: undefined}), tag({ownerTimeLimitDays: 90}));
      expect(effect).toContain('Ownership longer than 90 days');
    });

    it('says "1 day" rather than "1 days"', () => {
      const [effect] = tighteningEffects(tag({memberTimeLimitDays: 30}), tag({memberTimeLimitDays: 1}));
      expect(effect).toContain('1 day ');
      expect(effect).not.toContain('1 days');
    });

    it('names the roles only while the tag reaches them', () => {
      const reaching = tighteningEffects(
        tag({memberTimeLimitDays: 30}),
        tag({memberTimeLimitDays: 7, propagatesToRoles: true}),
      );
      const narrow = tighteningEffects(
        tag({memberTimeLimitDays: 30, propagatesToRoles: false}),
        tag({memberTimeLimitDays: 7, propagatesToRoles: false}),
      );
      expect(reaching[0]).toContain('roles that reach tagged groups');
      expect(narrow[0]).not.toContain('roles');
    });

    it('warns when the scope widens, which applies unchanged limits to role rosters', () => {
      // The limits themselves do not move, so the limit comparison alone would
      // report nothing while role membership is about to be shortened.
      const effects = tighteningEffects(
        tag({propagatesToRoles: false, memberTimeLimitDays: 7}),
        tag({propagatesToRoles: true, memberTimeLimitDays: 7}),
      );
      expect(effects).toHaveLength(1);
      expect(effects[0]).toContain('start applying to the roles');
      expect(effects[0]).toContain('shortening');
    });

    it('does not promise shortening when a widened scope carries no limit', () => {
      const [effect] = tighteningEffects(
        tag({propagatesToRoles: false, memberTimeLimitDays: undefined, ownerTimeLimitDays: undefined}),
        tag({propagatesToRoles: true, memberTimeLimitDays: undefined, ownerTimeLimitDays: undefined}),
      );
      expect(effect).toContain('start applying to the roles');
      expect(effect).not.toContain('shortening');
    });

    it('warns when a reason becomes required', () => {
      expect(tighteningEffects(tag(), tag({requireOwnerReason: true}))).toEqual([
        'A reason becomes required to grant ownership.',
      ]);
    });

    it('names a newly applied self-add restriction by its shared label', () => {
      const [effect] = tighteningEffects(tag(), tag({disallowSelfAddMembership: true}));
      expect(effect).toContain('Disallow adding oneself as a member');
    });

    it('lists every tightening change, since an admin is about to commit all of them', () => {
      const effects = tighteningEffects(
        tag({memberTimeLimitDays: 30, ownerTimeLimitDays: 30}),
        tag({memberTimeLimitDays: 7, ownerTimeLimitDays: 3, requireMemberReason: true}),
      );
      expect(effects).toHaveLength(3);
    });
  });

  // A disabled tag enforces nothing, so enabling one applies every constraint it
  // already stored to access that already exists -- the largest change this dialog
  // can make, and one no comparison of the constraints alone would notice.
  describe('enabling a tag', () => {
    const disabled = (overrides: Partial<TagSettings> = {}) => tag({enabled: false, ...overrides});

    it('warns about a stored limit that the edit never touches', () => {
      const [effect] = tighteningEffects(
        disabled({memberTimeLimitDays: 7}),
        tag({memberTimeLimitDays: 7, enabled: true}),
      );
      expect(effect).toContain('Membership longer than 7 days');
      expect(effect).toContain('shortened');
    });

    it('warns about a stored flag that the edit never touches', () => {
      // Limits cleared so the flag is the only thing the tag constrains.
      const flagOnly = {memberTimeLimitDays: undefined, ownerTimeLimitDays: undefined, requireOwnerReason: true};
      expect(tighteningEffects(disabled(flagOnly), tag({...flagOnly, enabled: true}))).toEqual([
        'A reason becomes required to grant ownership.',
      ]);
    });

    it('says nothing when the tag it enables carries no constraints', () => {
      const bare = {memberTimeLimitDays: undefined, ownerTimeLimitDays: undefined};
      expect(tighteningEffects(disabled(bare), tag({...bare, enabled: true}))).toEqual([]);
    });

    it('does not also claim the scope widens, which the limit sentence already covers', () => {
      const effects = tighteningEffects(
        disabled({memberTimeLimitDays: 7, ownerTimeLimitDays: undefined}),
        tag({memberTimeLimitDays: 7, ownerTimeLimitDays: undefined, enabled: true}),
      );
      expect(effects).toHaveLength(1);
      expect(effects[0]).toContain('roles that reach tagged groups');
    });
  });

  describe('a tag that is not enabled on save', () => {
    it('says nothing when disabling is the only change, since that enforces less', () => {
      expect(tighteningEffects(tag({memberTimeLimitDays: 7}), tag({memberTimeLimitDays: 7, enabled: false}))).toEqual(
        [],
      );
    });

    it('still reports a tightening edit made while disabling', () => {
      const [effect] = tighteningEffects(tag({memberTimeLimitDays: 30}), tag({memberTimeLimitDays: 7, enabled: false}));
      expect(effect).toContain('Membership longer than 7 days');
    });

    it('still reports a tightening edit to a tag that stays disabled', () => {
      const [effect] = tighteningEffects(
        tag({memberTimeLimitDays: 30, enabled: false}),
        tag({memberTimeLimitDays: 7, enabled: false}),
      );
      expect(effect).toContain('Membership longer than 7 days');
    });

    // Measured against what it stored, not against nothing: the admin should see
    // the edit they are making, not settings they left alone.
    it('treats raising a limit on a tag that stays disabled as loosening', () => {
      expect(
        tighteningEffects(
          tag({memberTimeLimitDays: 7, enabled: false}),
          tag({memberTimeLimitDays: 30, enabled: false}),
        ),
      ).toEqual([]);
    });

    it('offers a caveat naming enablement as what the effects wait on', () => {
      expect(DORMANT_UNTIL_ENABLED).toMatch(/enabled/);
    });
  });
});
