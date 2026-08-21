// Where a keystroke should launch its confetti from. Kept free of React and of
// confetti-cannon itself so the target and caret rules can be unit tested.

export interface Point {
  x: number;
  y: number;
}

// Free-text input types only. Confetti follows typed characters, so pickers,
// checkboxes, and — deliberately — password fields are left alone.
const TEXT_INPUT_TYPES = new Set(['text', 'search', 'url', 'email', 'tel', 'number']);

export function isTypingTarget(target: EventTarget | null): target is HTMLInputElement | HTMLTextAreaElement {
  if (target instanceof HTMLTextAreaElement) {
    return true;
  }
  if (target instanceof HTMLInputElement) {
    return TEXT_INPUT_TYPES.has(target.type);
  }
  return false;
}

// One offscreen canvas measures text for every field on the page.
let measureContext: CanvasRenderingContext2D | null | undefined;

function getMeasureContext() {
  if (measureContext === undefined) {
    measureContext = document.createElement('canvas').getContext('2d');
  }
  return measureContext;
}

// Width of `text` as `element` would render it, or null when it can't be measured.
function measureTextWidth(element: Element, text: string): number | null {
  const context = getMeasureContext();
  if (context == null) {
    return null;
  }

  const style = window.getComputedStyle(element);
  if (!style.fontSize || !style.fontFamily) {
    return null;
  }

  context.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
  return context.measureText(text).width;
}

function toNumber(value: string) {
  const parsed = parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

/**
 * Viewport point to launch a keystroke's confetti from: the caret itself in a
 * single-line input, or the middle of the field when the caret can't be located
 * (a textarea wraps, so its caret needs a full text layout to find).
 */
export function getTypingOrigin(element: HTMLInputElement | HTMLTextAreaElement): Point {
  const rect = element.getBoundingClientRect();
  const middle = {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2};

  if (element instanceof HTMLTextAreaElement) {
    return middle;
  }

  // selectionStart is null on input types that don't support selection
  // (number, email), where the caret always sits after the last character.
  const caretIndex = element.selectionStart ?? element.value.length;
  const textWidth = measureTextWidth(element, element.value.slice(0, caretIndex));
  if (textWidth == null) {
    return middle;
  }

  const style = window.getComputedStyle(element);
  const textStart = rect.left + toNumber(style.borderLeftWidth) + toNumber(style.paddingLeft);
  const caretX = textStart + textWidth - element.scrollLeft;
  // A caret scrolled out of view, or a long value in a narrow field, would
  // otherwise fire confetti outside the input.
  return {x: Math.min(Math.max(caretX, rect.left), rect.right), y: middle.y};
}
