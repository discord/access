import * as React from 'react';
import {describe, it, expect, afterEach} from 'vitest';
import {render, cleanup} from '@testing-library/react';
import createCache from '@emotion/cache';
import {CacheProvider} from '@emotion/react';
import Button from '@mui/material/Button';

// The production `style-src` admits inline styles only by nonce, so every `<style>` element
// MUI injects has to carry one or the browser drops it and the whole app renders unstyled.
// Emotion only stamps the nonce when its cache was built with one, which is easy to lose in a
// refactor of src/index.tsx and invisible to every other test, since jsdom enforces no CSP.
//
// This mirrors what src/index.tsx builds. Asserting on the real thing would mean importing the
// entry point, which mounts the app and initialises Sentry as a side effect.
const NONCE = 'test-nonce-value';

afterEach(cleanup);

describe('Emotion cache', () => {
  it('stamps the CSP nonce on the style elements it injects', () => {
    const cache = createCache({key: 'csp-test', nonce: NONCE});
    render(
      <CacheProvider value={cache}>
        <Button variant="contained">Renew</Button>
      </CacheProvider>,
    );

    const styles = document.querySelectorAll('style[data-emotion~="csp-test"]');
    expect(styles.length).toBeGreaterThan(0);
    styles.forEach((el) => expect(el.getAttribute('nonce')).toBe(NONCE));
  });

  it('omits the nonce when the shell did not publish one', () => {
    const cache = createCache({key: 'csp-test-none', nonce: undefined});
    render(
      <CacheProvider value={cache}>
        <Button variant="contained">Renew</Button>
      </CacheProvider>,
    );

    const styles = document.querySelectorAll('style[data-emotion~="csp-test-none"]');
    expect(styles.length).toBeGreaterThan(0);
    styles.forEach((el) => expect(el.getAttribute('nonce')).toBeNull());
  });
});
