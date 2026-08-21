import {describe, it, expect, beforeAll, afterAll, vi} from 'vitest';

import {getTypingOrigin, isTypingTarget} from './typingTargets';

// jsdom has no 2d canvas, so stand in a context whose text measurement is
// predictable: 10px per character.
beforeAll(() => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    font: '',
    measureText: (text: string) => ({width: text.length * 10}),
  } as unknown as CanvasRenderingContext2D);
});

afterAll(() => {
  vi.restoreAllMocks();
});

const RECT = {left: 100, top: 50, width: 200, height: 40, right: 300, bottom: 90, x: 100, y: 50};

function placeInDocument<T extends HTMLElement>(element: T): T {
  // jsdom lays nothing out, so every field gets the same stated geometry.
  element.getBoundingClientRect = () => ({...RECT, toJSON: () => RECT});
  document.body.appendChild(element);
  return element;
}

function textInput(value: string, caretIndex = value.length) {
  const input = document.createElement('input');
  input.style.fontSize = '10px';
  input.style.fontFamily = 'monospace';
  // Pinned so the expected caret offsets don't depend on jsdom's UA styles.
  input.style.borderLeftWidth = '1px';
  input.style.paddingLeft = '4px';
  input.value = value;
  placeInDocument(input);
  input.setSelectionRange(caretIndex, caretIndex);
  return input;
}

describe('isTypingTarget', () => {
  it('accepts free-text inputs and textareas', () => {
    const untyped = document.createElement('input');
    expect(isTypingTarget(untyped)).toBe(true);

    for (const type of ['text', 'search', 'url', 'email', 'tel', 'number']) {
      const input = document.createElement('input');
      input.type = type;
      expect(isTypingTarget(input)).toBe(true);
    }

    expect(isTypingTarget(document.createElement('textarea'))).toBe(true);
  });

  it('leaves password fields alone', () => {
    const password = document.createElement('input');
    password.type = 'password';
    expect(isTypingTarget(password)).toBe(false);
  });

  it('ignores inputs that are not typed into, and non-inputs', () => {
    for (const type of ['checkbox', 'radio', 'range', 'file', 'color', 'submit']) {
      const input = document.createElement('input');
      input.type = type;
      expect(isTypingTarget(input)).toBe(false);
    }

    expect(isTypingTarget(document.createElement('div'))).toBe(false);
    expect(isTypingTarget(document.createElement('button'))).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
  });
});

describe('getTypingOrigin', () => {
  it('follows the caret across an input', () => {
    // Field at x=100 with 5px of border and padding, 4 characters at 10px each.
    expect(getTypingOrigin(textInput('abcd'))).toEqual({x: 145, y: 70});
    // Same value, caret parked after the first character.
    expect(getTypingOrigin(textInput('abcd', 1))).toEqual({x: 115, y: 70});
  });

  it('keeps confetti inside a field the text has overflowed', () => {
    // 40 characters measure 400px in a 200px-wide field.
    expect(getTypingOrigin(textInput('a'.repeat(40)))).toEqual({x: RECT.right, y: 70});
  });

  it('falls back to the middle of a textarea, whose caret needs a text layout', () => {
    const textarea = placeInDocument(document.createElement('textarea'));
    textarea.value = 'a wrapped\nreason';
    expect(getTypingOrigin(textarea)).toEqual({x: 200, y: 70});
  });
});
