import {render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {describe, expect, it} from 'vitest';

import {ConstraintHelpButton, ConstraintHelpRegion, useConstraintHelp, useHelpRegionId} from './ConstraintHelp';

const PARAGRAPHS = [{lead: 'When it reaches roles', text: ', the same limit applies to the role.'}];

// The two halves of a disclosure as a surface wires them: one hook, a button
// placed one way and a region another, agreeing on the id between them.
function Disclosure({constraint = 'member_time_limit'}: {constraint?: string}) {
  const help = useConstraintHelp(constraint);
  return (
    <>
      <ConstraintHelpButton label="Membership time limit" {...help} onToggle={help.toggle} />
      <ConstraintHelpRegion sections={[{paragraphs: PARAGRAPHS}]} expanded={help.expanded} regionId={help.regionId} />
    </>
  );
}

describe('useConstraintHelp', () => {
  it('starts closed and opens on click', async () => {
    render(<Disclosure />);
    const button = screen.getByRole('button');
    expect(button).toHaveAttribute('aria-expanded', 'false');
    await userEvent.click(button);
    expect(button).toHaveAttribute('aria-expanded', 'true');
  });

  // Two surfaces render the same constraint keys at once: the tag page lists a
  // tag's constraints while its edit dialog sets them. An id built from the key
  // alone appears twice in one document, and `aria-controls` then resolves to
  // whichever the tree happens to hold first.
  it('gives two instances of the same constraint different region ids', async () => {
    render(
      <>
        <Disclosure />
        <Disclosure />
      </>,
    );
    const [first, second] = screen.getAllByRole('button');
    await userEvent.click(first);
    await userEvent.click(second);
    const ids = screen.getAllByRole('button').map((button) => button.getAttribute('aria-controls'));
    expect(ids[0]).not.toBe(ids[1]);
    expect(document.querySelectorAll(`[id="${ids[0]}"]`)).toHaveLength(1);
  });

  it('builds an id that is safe to use as a CSS selector', async () => {
    // React's `useId` is free to emit punctuation (`:r1:`), which needs escaping
    // in a selector. Callers should not have to know that.
    render(<Disclosure />);
    await userEvent.click(screen.getByRole('button'));
    const id = screen.getByRole('button').getAttribute('aria-controls');
    expect(id).toMatch(/^[A-Za-z0-9_-]+$/);
  });
});

describe('useHelpRegionId', () => {
  it('scopes every key it is asked for to the same instance', () => {
    function TwoKeys() {
      const regionId = useHelpRegionId();
      return <div data-testid="ids">{`${regionId('member_time_limit')} ${regionId('owner_time_limit')}`}</div>;
    }
    render(
      <>
        <TwoKeys />
        <TwoKeys />
      </>,
    );
    const [first, second] = screen.getAllByTestId('ids').map((node) => node.textContent!.split(' '));
    // Distinct within an instance, and no id shared across instances.
    expect(first[0]).not.toBe(first[1]);
    expect(new Set([...first, ...second]).size).toBe(4);
  });
});

describe('ConstraintHelpButton', () => {
  // `aria-controls` must name an element that exists. The region is unmounted
  // while collapsed -- by `unmountOnExit` here, and by the effective-constraints
  // panel dropping the whole table row -- so a permanent IDREF dangles in the
  // default state of every constraint on the page.
  it('names no region while the help is closed', () => {
    render(<Disclosure />);
    expect(screen.getByRole('button')).not.toHaveAttribute('aria-controls');
  });

  it('names the region it actually reveals once open', async () => {
    render(<Disclosure />);
    const button = screen.getByRole('button');
    await userEvent.click(button);
    const id = button.getAttribute('aria-controls');
    expect(id).toBeTruthy();
    expect(document.getElementById(id!)).not.toBeNull();
  });
});

describe('ConstraintHelpRegion', () => {
  it('renders nothing when no section has any copy', () => {
    const {container} = render(<ConstraintHelpRegion sections={[{paragraphs: []}]} expanded regionId="r" />);
    expect(container).toBeEmptyDOMElement();
  });
});
