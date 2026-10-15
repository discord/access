import * as React from 'react';
import {render, screen, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {describe, expect, it, vi} from 'vitest';

import {AppDetail, TagListItem, TagSummary} from '../../api/apiSchemas';
import TagsField, {inheritedAppTags} from './TagsField';

const SOX = {id: 'sox-tag-00000000000', name: 'SOX'};
const QUARTERLY = {id: 'quarterly-review-01', name: 'Quarterly Renewal'};

const ledger = (...tags: TagSummary[]) =>
  ({name: 'Ledger', active_app_tags: tags.map((tag) => ({active_tag: tag}))}) as unknown as AppDetail;

const tag = (source: TagSummary): TagListItem => ({...source, created_at: null, updated_at: null});

describe('the tags an app supplies', () => {
  it('reports them against the app supplying them', () => {
    expect(inheritedAppTags(ledger(SOX), [])).toEqual({appName: 'Ledger', tags: [SOX]});
  });

  // Picked deliberately, it is submitted as a tag of the group's own, so the
  // field owes the requester a chip they can take back off.
  it('leaves out one the form has already chosen', () => {
    expect(inheritedAppTags(ledger(SOX, QUARTERLY), [SOX.id])).toEqual({appName: 'Ledger', tags: [QUARTERLY]});
  });

  it('reports none when every one of them was chosen on the form', () => {
    expect(inheritedAppTags(ledger(SOX), [SOX.id])).toBeNull();
  });

  it('reports none for an app carrying no tags', () => {
    expect(inheritedAppTags(ledger(), [])).toBeNull();
  });

  it('reports none while the app is still loading', () => {
    expect(inheritedAppTags(undefined, [])).toBeNull();
  });
});

describe('the Tags field', () => {
  const renderField = (onSelectedChange = vi.fn()) => {
    render(
      <TagsField
        options={[tag(SOX), tag(QUARTERLY)]}
        selected={[tag(QUARTERLY)]}
        inherited={inheritedAppTags(ledger(SOX), [QUARTERLY.id])}
        onSelectedChange={onSelectedChange}
        onSearchChange={vi.fn()}
      />,
    );
    return onSelectedChange;
  };

  const chipFor = (label: string) => screen.getByText(label).closest('.MuiChip-root') as HTMLElement;

  it('names the app on an inherited chip and not on a chosen one', () => {
    renderField();

    expect(screen.getByText('SOX (Ledger)')).toBeInTheDocument();
    expect(screen.getByText(QUARTERLY.name)).toBeInTheDocument();
  });

  it('lists a chosen tag with a delete control and an inherited one without', () => {
    renderField();

    expect(within(chipFor(QUARTERLY.name)).getByTestId('CancelIcon')).toBeInTheDocument();
    expect(within(chipFor('SOX (Ledger)')).queryByTestId('CancelIcon')).toBeNull();
  });

  it('reports only the chosen tags when one is removed', async () => {
    const onSelectedChange = renderField();

    await userEvent.click(within(chipFor(QUARTERLY.name)).getByTestId('CancelIcon'));

    expect(onSelectedChange).toHaveBeenCalledWith([]);
  });

  // Backspace on an empty input deletes the last chip whatever it is, so the
  // field has to put the inherited ones back rather than let one be typed away.
  it('keeps an inherited tag through a backspace that would delete it', async () => {
    const onSelectedChange = vi.fn();
    render(
      <TagsField
        options={[tag(SOX)]}
        selected={[]}
        inherited={inheritedAppTags(ledger(SOX), [])}
        onSelectedChange={onSelectedChange}
        onSearchChange={vi.fn()}
      />,
    );

    await userEvent.click(screen.getByRole('combobox', {name: 'Tags'}));
    await userEvent.keyboard('{Backspace}');

    expect(onSelectedChange).not.toHaveBeenCalledWith(expect.arrayContaining([expect.objectContaining({id: SOX.id})]));
    expect(screen.getByText('SOX (Ledger)')).toBeInTheDocument();
  });

  // It is already on the group; offering it again would submit a second,
  // group-owned tag map that outlives the app's.
  it('refuses to let an inherited tag be picked from the menu', async () => {
    renderField();

    await userEvent.click(screen.getByRole('button', {name: 'Open'}));

    expect(screen.getByRole('option', {name: SOX.name})).toHaveAttribute('aria-disabled', 'true');
  });
});
