import {describe, it, expect} from 'vitest';
import {act, renderHook} from '@testing-library/react';

import {useSearchInput} from './useSearchInput';

function render(initial: string) {
  return renderHook(({params}) => useSearchInput(params), {
    initialProps: {params: new URLSearchParams(initial)},
  });
}

describe('useSearchInput', () => {
  it('seeds the input from the q param', () => {
    const {result} = render('q=alpha');
    expect(result.current[0]).toBe('alpha');
  });

  it('starts empty without a q param', () => {
    const {result} = render('page=2');
    expect(result.current[0]).toBe('');
  });

  it('keeps a non-empty input when the params change', () => {
    const {result, rerender} = render('q=alpha');
    act(() => result.current[1]('typing'));
    rerender({params: new URLSearchParams('q=alpha&page=3')});
    expect(result.current[0]).toBe('typing');
  });

  it('re-seeds an emptied input only when the params change', () => {
    const {result, rerender} = render('q=alpha');
    act(() => result.current[1](''));
    expect(result.current[0]).toBe('');
    rerender({params: new URLSearchParams('q=beta')});
    expect(result.current[0]).toBe('beta');
  });
});
