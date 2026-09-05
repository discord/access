import {describe, it, expect} from 'vitest';
import {render, screen} from '@testing-library/react';

import MarkdownDescription, {firstParagraph} from './MarkdownDescription';

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

  it('ignores leading blank lines before finding the first paragraph', () => {
    expect(firstParagraph('\n\nSecond para')).toBe('Second para');
  });

  it('ignores leading blank lines and still marks that more follows', () => {
    expect(firstParagraph('\n\nSecond para\n\nThird')).toBe('Second para ...');
  });
});

describe('MarkdownDescription inline rendering', () => {
  it('renders only the first paragraph and marks that more follows', () => {
    render(
      <MarkdownDescription description={'Owners of the Zendesk application\n\nAlso grants billing access'} inline />,
    );

    expect(screen.getByText(/Owners of the Zendesk application \.\.\./)).toBeInTheDocument();
    expect(screen.queryByText(/Also grants billing access/)).not.toBeInTheDocument();
  });

  it('renders a single-paragraph description without an ellipsis', () => {
    render(<MarkdownDescription description="Owners of the Zendesk application" inline />);

    expect(screen.getByText('Owners of the Zendesk application')).toBeInTheDocument();
    expect(screen.queryByText(/\.\.\./)).not.toBeInTheDocument();
  });

  it('still renders every paragraph in block mode', () => {
    render(<MarkdownDescription description={'First para\n\nSecond para'} />);

    expect(screen.getByText('First para')).toBeInTheDocument();
    expect(screen.getByText('Second para')).toBeInTheDocument();
  });
});
