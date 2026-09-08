import {describe, it, expect} from 'vitest';

import {
  appOwnerGroupDescriptionPrefix,
  appOwnerGroupDescriptionRemainder,
  appOwnerGroupDescriptionRemainderMaxLength,
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

describe('appOwnerGroupDescriptionRemainderMaxLength', () => {
  it('caps the remainder so a description composed at that length is exactly 1024 characters', () => {
    const appName = 'Zendesk';
    const maxLength = appOwnerGroupDescriptionRemainderMaxLength(appName);
    const remainderAtLimit = 'x'.repeat(maxLength);

    expect(composeAppOwnerGroupDescription(appName, remainderAtLimit)).toHaveLength(1024);
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

// Mirrors tests/test_app_group_description.py one-for-one so the two can be diffed.
describe('markdown indentation parity with the backend', () => {
  // test_remainder_preserves_indentation_on_a_conforming_description
  it('preserves indentation on the remainder of a conforming description', () => {
    const description = 'Owners of the Zendesk application\n\n    - nested item\n    - another item';
    expect(appOwnerGroupDescriptionRemainder(description, 'Zendesk')).toBe('    - nested item\n    - another item');
  });

  // test_remainder_preserves_indentation_on_a_non_conforming_description
  it('preserves indentation on a non-conforming description', () => {
    const description = '    - nested item\n    - another item';
    expect(appOwnerGroupDescriptionRemainder(description, 'Zendesk')).toBe('    - nested item\n    - another item');
  });

  // Covers composeAppOwnerGroupDescription's own use of the same helper.
  it('preserves indentation when composing with an indented remainder', () => {
    expect(composeAppOwnerGroupDescription('Zendesk', '    - nested item\n    - another item')).toBe(
      'Owners of the Zendesk application\n\n    - nested item\n    - another item',
    );
  });

  // test_compose_and_split_round_trip_preserves_indentation
  it('round-trips an indented remainder through compose and remainder exactly', () => {
    const composed = composeAppOwnerGroupDescription('Zendesk', '    - nested item\n    - another item');
    expect(appOwnerGroupDescriptionRemainder(composed, 'Zendesk')).toBe('    - nested item\n    - another item');
    expect(composeAppOwnerGroupDescription('Zendesk', appOwnerGroupDescriptionRemainder(composed, 'Zendesk'))).toBe(
      composed,
    );
  });

  // test_composing_after_a_crlf_normalises_the_separator
  it('still removes leading blank lines, including a CRLF blank-line separator', () => {
    expect(
      appOwnerGroupDescriptionRemainder('Owners of the Zendesk application\n\n\n\n   \nActual text', 'Zendesk'),
    ).toBe('Actual text');
  });
});
