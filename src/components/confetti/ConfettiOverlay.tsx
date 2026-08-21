import * as React from 'react';
import {rgbToHex, useTheme} from '@mui/material/styles';
import {
  ConfettiCanvas,
  ConfettiCanvasHandle,
  CreateConfettiArgs,
  Environment,
  SpriteCanvas,
  SpriteCanvasHandle,
  SpriteProp,
  useConfettiCannon,
} from 'confetti-cannon';

import {getTypingOrigin, isTypingTarget, Point} from './typingTargets';

// confetti-cannon scales its drawing by `global.devicePixelRatio` — a Node-ism
// its own webpack build shimmed away, and one Vite does not provide. Point
// `global` at the browser's global object before any confetti is drawn. This
// module is only loaded once the cannon is switched on, so nothing else in the
// app is touched.
(globalThis as {global?: typeof globalThis}).global ??= globalThis;

// confetti-cannon pre-renders its confetti onto an offscreen sprite sheet, one
// row per shape and one column per color, so every shape has to arrive as an
// image. Inline SVG data URIs keep them in the bundle. The shapes are solid
// black because colorizing rewrites every pixel's color and keeps only the
// alpha channel, which is where the shape actually lives.
const spriteDataUri = (shape: string) =>
  `data:image/svg+xml;utf8,${encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" fill="none" width="8" height="8" viewBox="0 0 8 8">${shape}</svg>`,
  )}`;

const SPRITES: SpriteProp[] = [
  spriteDataUri('<rect width="8" height="8" fill="#000"/>'),
  spriteDataUri('<circle cx="4" cy="4" r="4" fill="#000"/>'),
  spriteDataUri('<rect y="2" width="8" height="4" rx="2" fill="#000"/>'),
  spriteDataUri('<polygon points="4,0 8,8 0,8" fill="#000"/>'),
];

const MIN_CONFETTI_SIZE = 6;
const MAX_CONFETTI_SIZE = 14;
// A keystroke is a small puff at the caret; switching the cannon on is a real
// celebration around the button. `SPREAD` is how far from that point, in
// pixels, a piece can start out.
const CONFETTI_PER_KEYSTROKE = 4;
const KEYSTROKE_SPREAD = 6;
const CONFETTI_PER_CELEBRATION = 45;
const CELEBRATION_SPREAD = 12;
// Fast typists outrun the animation otherwise, and the screen turns to soup.
const KEYSTROKE_THROTTLE_MS = 70;

export default function ConfettiOverlay({launchFrom}: {launchFrom: Point | null}) {
  const theme = useTheme();
  const [confettiCanvas, setConfettiCanvas] = React.useState<ConfettiCanvasHandle | null>(null);
  const [spriteCanvas, setSpriteCanvas] = React.useState<SpriteCanvasHandle | null>(null);
  const environment = React.useMemo(() => new Environment(), []);
  const cannon = useConfettiCannon(confettiCanvas, spriteCanvas);

  // Confetti wears the app's own palette. The sprite sheet colorizes from hex
  // only, so normalize through rgbToHex and drop any alpha an 8-digit result
  // carries — sprite transparency comes from the shape, not the color.
  const colors = React.useMemo(
    () =>
      [
        theme.palette.primary.main,
        theme.palette.primary.light,
        theme.palette.secondary.main,
        theme.palette.success.main,
        theme.palette.warning.main,
        theme.palette.error.main,
        theme.palette.info.main,
      ].map((color) => rgbToHex(color).slice(0, 7)),
    [theme],
  );

  const fire = React.useCallback(
    (origin: Point, count: number, spread: number) => {
      const args: CreateConfettiArgs = {
        position: {
          type: 'static-random',
          minValue: {x: origin.x - spread, y: origin.y - spread},
          maxValue: {x: origin.x + spread, y: origin.y + spread},
        },
        velocity: {
          type: 'static-random',
          minValue: {x: -18, y: -25},
          maxValue: {x: 18, y: -50},
        },
        rotation: {
          type: 'linear-random',
          minValue: 0,
          maxValue: 360,
          minAddValue: -25,
          maxAddValue: 25,
        },
        size: {
          type: 'static-random',
          minValue: MIN_CONFETTI_SIZE,
          maxValue: MAX_CONFETTI_SIZE,
          // Keep each piece's aspect ratio so circles stay round.
          uniformVectorValues: true,
        },
        opacity: {type: 'linear', value: 1, addValue: -0.07},
      };
      cannon.createMultipleConfetti(args, count);
    },
    [cannon],
  );

  // Fire from the button that switched the cannon on, once the canvases are up.
  const celebrated = React.useRef(false);
  React.useEffect(() => {
    if (launchFrom == null || !cannon.isReady || celebrated.current) {
      return;
    }
    celebrated.current = true;
    fire(launchFrom, CONFETTI_PER_CELEBRATION, CELEBRATION_SPREAD);
  }, [cannon.isReady, fire, launchFrom]);

  React.useEffect(() => {
    if (!cannon.isReady) {
      return;
    }

    let lastFiredAt = 0;
    const handleInput = (event: Event) => {
      if (!isTypingTarget(event.target)) {
        return;
      }
      const now = Date.now();
      if (now - lastFiredAt < KEYSTROKE_THROTTLE_MS) {
        return;
      }
      lastFiredAt = now;
      fire(getTypingOrigin(event.target), CONFETTI_PER_KEYSTROKE, KEYSTROKE_SPREAD);
    };

    // Capture phase so a field that stops propagation still gets its confetti.
    document.addEventListener('input', handleInput, true);
    return () => document.removeEventListener('input', handleInput, true);
  }, [cannon.isReady, fire]);

  const canvasStyle = React.useMemo<React.CSSProperties>(
    () => ({
      position: 'fixed',
      inset: 0,
      // A canvas has an intrinsic size, so inset alone won't stretch it.
      width: '100%',
      height: '100%',
      // Confetti is never in the way: the canvas covers dialogs and the app
      // bar, but every click passes straight through it.
      pointerEvents: 'none',
      zIndex: theme.zIndex.tooltip + 1,
    }),
    [theme.zIndex.tooltip],
  );

  return (
    <>
      <SpriteCanvas
        ref={setSpriteCanvas}
        sprites={SPRITES}
        colors={colors}
        spriteWidth={MAX_CONFETTI_SIZE}
        spriteHeight={MAX_CONFETTI_SIZE}
      />
      <ConfettiCanvas ref={setConfettiCanvas} environment={environment} style={canvasStyle} aria-hidden />
    </>
  );
}
