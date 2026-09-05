import {describe, it, expect} from 'vitest';

import {firstParagraph} from './MarkdownDescription';

describe('firstParagraph', () => {
  it('returns a single-paragraph description unchanged, with no ellipsis', () => {
    expect(firstParagraph('Owners of the Zendesk application')).toBe('Owners of the Zendesk application');
  });

  it('keeps only the first paragraph and marks that more follows', () => {
    expect(firstParagraph('Owners of the Zendesk application\n\nAlso grants billing access')).toBe(
      'Owners of the Zendesk application ...',
    );
  });

  it('treats a CRLF blank line as a paragraph break', () => {
    expect(firstParagraph('First para\r\n\r\nSecond para')).toBe('First para ...');
  });

  it('treats a blank line containing whitespace as a paragraph break', () => {
    expect(firstParagraph('First para\n   \nSecond para')).toBe('First para ...');
  });

  it('does not add an ellipsis when nothing follows the blank line', () => {
    expect(firstParagraph('Only para\n\n')).toBe('Only para');
    expect(firstParagraph('Only para\n\n   \n  ')).toBe('Only para');
  });

  it('returns an empty string for an empty description', () => {
    expect(firstParagraph('')).toBe('');
  });
});
