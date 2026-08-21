import {describe, it, expect} from 'vitest';

import SPRITES from './sprites';

describe('confetti sprites', () => {
  // Regression guard: inlined `data:` sprites are blocked by the app's CSP,
  // which has no `img-src` and so falls back to `default-src 'self'`. A blocked
  // sprite never fires `onload`, and the cannon then sits at not-ready without
  // reporting anything — so this failure mode is invisible at runtime.
  it('are same-origin paths the app CSP allows', () => {
    expect(SPRITES.length).toBeGreaterThan(0);
    for (const sprite of SPRITES) {
      expect(sprite).toMatch(/^\/[\w/-]+\.svg$/);
    }
  });
});
