import {describe, expect, it} from 'vitest';

import {TagSettings, tighteningEffects} from './tagChanges';

const SETTINGS: TagSettings = {
  memberTimeLimitDays: 30,
  ownerTimeLimitDays: 30,
  requireMemberReason: false,
  requireOwnerReason: false,
  disallowSelfAddMembership: false,
  disallowSelfAddOwnership: false,
  propagatesToRoles: true,
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
});
