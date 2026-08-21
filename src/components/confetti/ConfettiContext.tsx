import * as React from 'react';
import useMediaQuery from '@mui/material/useMediaQuery';

import {Point} from './typingTargets';

// The confetti machinery — and confetti-cannon itself — is only worth
// downloading once someone has found the switch.
const ConfettiOverlay = React.lazy(() => import('./ConfettiOverlay'));

/** Consecutive theme-toggle clicks that reveal the confetti button. */
export const CLICKS_TO_UNLOCK = 3;
/** A click this long after the previous one starts the count over. */
export const CLICK_WINDOW_MS = 3000;

const UNLOCKED_STORAGE_KEY = 'confetti-cannon-unlocked';
const ENABLED_STORAGE_KEY = 'confetti-cannon-enabled';

interface ConfettiContextValue {
  /** Whether the toggle has been discovered, and so is rendered at all. */
  unlocked: boolean;
  enabled: boolean;
  /** The user's system asks for less motion, so confetti stays in its box. */
  reducedMotion: boolean;
  /** Counts a theme-toggle click towards revealing the confetti button. */
  registerThemeToggleClick: () => void;
  /** Turns the cannon on or off; `origin` gets a burst when turning it on. */
  setEnabled: (enabled: boolean, origin?: Point) => void;
}

// Defaults keep the theme toggle usable in isolation (tests, storybooks).
const ConfettiContext = React.createContext<ConfettiContextValue>({
  unlocked: false,
  enabled: false,
  reducedMotion: false,
  registerThemeToggleClick: () => {},
  setEnabled: () => {},
});

export function useConfetti() {
  return React.useContext(ConfettiContext);
}

export function ConfettiProvider({children}: {children: React.ReactNode}) {
  const [unlocked, setUnlocked] = React.useState(() => localStorage.getItem(UNLOCKED_STORAGE_KEY) === 'true');
  const [enabled, setEnabledState] = React.useState(() => localStorage.getItem(ENABLED_STORAGE_KEY) === 'true');
  // Only set when the user flips the switch, so a reload doesn't fire a burst.
  const [launchFrom, setLaunchFrom] = React.useState<Point | null>(null);
  const reducedMotion = useMediaQuery('(prefers-reduced-motion: reduce)');

  const clickCount = React.useRef(0);
  const lastClickAt = React.useRef(0);

  const registerThemeToggleClick = React.useCallback(() => {
    const now = Date.now();
    clickCount.current = now - lastClickAt.current > CLICK_WINDOW_MS ? 1 : clickCount.current + 1;
    lastClickAt.current = now;

    if (clickCount.current >= CLICKS_TO_UNLOCK) {
      clickCount.current = 0;
      setUnlocked(true);
      localStorage.setItem(UNLOCKED_STORAGE_KEY, 'true');
    }
  }, []);

  const setEnabled = React.useCallback((next: boolean, origin?: Point) => {
    setEnabledState(next);
    localStorage.setItem(ENABLED_STORAGE_KEY, String(next));
    setLaunchFrom(next ? origin ?? null : null);
  }, []);

  const value = React.useMemo(
    () => ({unlocked, enabled, reducedMotion, registerThemeToggleClick, setEnabled}),
    [unlocked, enabled, reducedMotion, registerThemeToggleClick, setEnabled],
  );

  return (
    <ConfettiContext.Provider value={value}>
      {children}
      {enabled && !reducedMotion && (
        <React.Suspense fallback={null}>
          <ConfettiOverlay launchFrom={launchFrom} />
        </React.Suspense>
      )}
    </ConfettiContext.Provider>
  );
}
