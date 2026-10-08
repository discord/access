import * as React from 'react';
import {describe, it} from 'vitest';
import {render, act} from '@testing-library/react';
import {styled} from '@mui/material/styles';

// MUI X calls React hooks from inside a styled() interpolation: GridRootStyles reads
// useGridPrivateApiContext() and useGridSelector() to decide a border radius. That is only
// safe under an engine that re-evaluates interpolations on every render, which Emotion does
// and styled-components does not -- it memoizes the interpolation result and serves it
// whenever theme, stylesheet and props are unchanged, so the hooks inside vanish on the
// second render and React throws "Rendered fewer hooks than expected" (minified error #300).
// It surfaced as every Bulk Review dialog crashing the moment the grid re-rendered.
//
// This is the one property of the style engine the app depends on for correctness, and
// nothing else in the test suite would notice it changing, so pin it here rather than
// leaving a comment in vite.config.ts that a future alias could quietly contradict.
const Ctx = React.createContext(0);

const StyledWithHooks = styled('div')(() => {
  React.useContext(Ctx);
  React.useRef(null);
  React.useState(0);
  return {color: 'red'};
});

function Harness({onRender}: {onRender: (rerender: () => void) => void}) {
  const [, setTick] = React.useState(0);
  onRender(() => setTick((n) => n + 1));
  // Props are referentially identical across renders, which is what lets a memoizing
  // engine skip the interpolation.
  return <StyledWithHooks className="stable">child</StyledWithHooks>;
}

describe('MUI style engine', () => {
  it('re-evaluates styled() interpolations, so they may call hooks', () => {
    let rerender = () => {};
    render(<Harness onRender={(fn) => (rerender = fn)} />);
    act(() => rerender());
  });
});
