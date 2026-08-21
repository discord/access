import * as React from 'react';
import {describe, it, expect, afterEach, beforeEach, vi} from 'vitest';
import {act, render, screen} from '@testing-library/react';

// The overlay draws to a canvas and observes its size, neither of which jsdom
// has; stand in a marker so tests can still see when it is mounted.
vi.mock('./ConfettiOverlay', async () => {
  const react = await import('react');
  return {default: () => react.createElement('div', {'data-testid': 'overlay'})};
});

// MUI's useMediaQuery reaches for the styled engine, which the test environment
// doesn't have. Stub the one query the provider asks about.
const media = vi.hoisted(() => ({prefersReducedMotion: false}));
vi.mock('@mui/material/useMediaQuery', () => ({default: () => media.prefersReducedMotion}));

import {ConfettiProvider, CLICKS_TO_UNLOCK, CLICK_WINDOW_MS, useConfetti} from './ConfettiContext';

// A stand-in for the drawer footer: reports what the user would see, and lets a
// test click the theme buttons as quickly or as slowly as it likes.
let clickTheme: () => void;
let setEnabled: (enabled: boolean) => void;

function DrawerFooterProbe() {
  const context = useConfetti();
  clickTheme = context.registerThemeToggleClick;
  setEnabled = context.setEnabled;
  return (
    <>
      <div data-testid="unlocked">{String(context.unlocked)}</div>
      <div data-testid="enabled">{String(context.enabled)}</div>
    </>
  );
}

const renderProbe = () =>
  render(
    <ConfettiProvider>
      <DrawerFooterProbe />
    </ConfettiProvider>,
  );

const shows = (testId: string) => screen.getByTestId(testId).textContent === 'true';

const clickThemeTimes = (times: number, gapMs = 100) =>
  act(() => {
    for (let i = 0; i < times; i++) {
      vi.advanceTimersByTime(gapMs);
      clickTheme();
    }
  });

// The lazily-loaded overlay lands a microtask after the switch is flipped.
const settle = () => act(async () => {});

beforeEach(() => {
  localStorage.clear();
  media.prefersReducedMotion = false;
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('revealing the confetti button', () => {
  it('stays hidden until the theme toggle is clicked enough times in a row', () => {
    renderProbe();
    expect(shows('unlocked')).toBe(false);

    clickThemeTimes(CLICKS_TO_UNLOCK - 1);
    expect(shows('unlocked')).toBe(false);

    clickThemeTimes(1);
    expect(shows('unlocked')).toBe(true);
  });

  it('starts the count over when the clicks are too far apart', () => {
    renderProbe();

    // One click short of the reveal, and then one that lands too late to count
    // towards it: it starts a fresh streak of its own instead.
    clickThemeTimes(CLICKS_TO_UNLOCK - 1);
    clickThemeTimes(1, CLICK_WINDOW_MS + 1);
    expect(shows('unlocked')).toBe(false);

    // So the reveal is still one click away, not already behind us.
    clickThemeTimes(CLICKS_TO_UNLOCK - 2);
    expect(shows('unlocked')).toBe(false);
    clickThemeTimes(1);
    expect(shows('unlocked')).toBe(true);
  });

  it('stays revealed on the next visit', () => {
    const {unmount} = renderProbe();
    clickThemeTimes(CLICKS_TO_UNLOCK);
    expect(shows('unlocked')).toBe(true);
    unmount();

    renderProbe();
    expect(shows('unlocked')).toBe(true);
  });
});

describe('the cannon switch', () => {
  it('starts off and remembers being left on', async () => {
    const {unmount} = renderProbe();
    expect(shows('enabled')).toBe(false);
    expect(screen.queryByTestId('overlay')).toBeNull();

    act(() => setEnabled(true));
    await settle();
    expect(shows('enabled')).toBe(true);
    expect(screen.queryByTestId('overlay')).not.toBeNull();
    unmount();

    renderProbe();
    await settle();
    expect(shows('enabled')).toBe(true);
    expect(screen.queryByTestId('overlay')).not.toBeNull();
  });

  it('remembers being switched back off', async () => {
    const {unmount} = renderProbe();
    act(() => setEnabled(true));
    await settle();
    act(() => setEnabled(false));
    unmount();

    renderProbe();
    await settle();
    expect(shows('enabled')).toBe(false);
    expect(screen.queryByTestId('overlay')).toBeNull();
  });

  it('keeps the confetti grounded when the system asks for reduced motion', async () => {
    media.prefersReducedMotion = true;
    renderProbe();

    act(() => setEnabled(true));
    await settle();
    // The switch still reads as on, so it holds the preference for later.
    expect(shows('enabled')).toBe(true);
    expect(screen.queryByTestId('overlay')).toBeNull();
  });
});
