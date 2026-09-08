import {describe, it, expect} from 'vitest';

import {
  appOwnerGroupDescriptionPrefix,
  appOwnerGroupDescriptionRemainder,
  composeAppOwnerGroupDescription,
} from './appOwnerGroupDescription';

describe('appOwnerGroupDescriptionPrefix', () => {
  // Pinned literal. The backend owns this format string in
  // `app_owners_group_description` (api/models/app_group.py); if you change the
  // wording there, change it here and update both tests.
  it('matches the backend base line exactly', () => {
    expect(appOwnerGroupDescriptionPrefix('Zendesk')).toBe('Owners of the Zendesk application');
  });
});

describe('appOwnerGroupDescriptionRemainder', () => {
  it('returns the text after a matching base line', () => {
    expect(
      appOwnerGroupDescriptionRemainder('Owners of the Zendesk application\n\nAlso grants billing', 'Zendesk'),
    ).toBe('Also grants billing');
  });

  it('returns an empty string for a bare base line', () => {
    expect(appOwnerGroupDescriptionRemainder('Owners of the Zendesk application', 'Zendesk')).toBe('');
  });

  it('returns the whole description when the base line does not match', () => {
    expect(appOwnerGroupDescriptionRemainder('Hand-written legacy text', 'Zendesk')).toBe('Hand-written legacy text');
  });

  it('does not false-match a similarly named app', () => {
    expect(appOwnerGroupDescriptionRemainder('Owners of the FooBar application', 'Foo')).toBe(
      'Owners of the FooBar application',
    );
  });

  it('normalises CRLF', () => {
    expect(appOwnerGroupDescriptionRemainder('Owners of the Zendesk application\r\n\r\nExtra', 'Zendesk')).toBe(
      'Extra',
    );
  });

  it('returns an empty string for an empty description', () => {
    expect(appOwnerGroupDescriptionRemainder('', 'Zendesk')).toBe('');
  });
});

describe('composeAppOwnerGroupDescription', () => {
  it('returns the bare base line for an empty remainder', () => {
    expect(composeAppOwnerGroupDescription('Zendesk', '')).toBe('Owners of the Zendesk application');
    expect(composeAppOwnerGroupDescription('Zendesk', '   ')).toBe('Owners of the Zendesk application');
  });

  it('joins the base line and the remainder with a blank line', () => {
    expect(composeAppOwnerGroupDescription('Zendesk', 'Also grants billing')).toBe(
      'Owners of the Zendesk application\n\nAlso grants billing',
    );
  });

  it('round-trips with the remainder helper', () => {
    const composed = composeAppOwnerGroupDescription('Zendesk', 'Also grants billing');
    expect(appOwnerGroupDescriptionRemainder(composed, 'Zendesk')).toBe('Also grants billing');
  });
});
