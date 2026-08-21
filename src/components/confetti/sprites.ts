// The shapes confetti-cannon pre-renders onto its offscreen sprite sheet, one
// row per shape and one column per color.
//
// These must stay same-origin URLs, not inlined `data:` URIs. The app's CSP
// (api/middleware.py, `build_csp`) declares no `img-src`, so images fall back to
// `default-src 'self'` and a `data:` URI is blocked outright. SpriteCanvas only
// ever resolves its load promise on `image.onload` — there is no `onerror`
// path — so a blocked sprite leaves the cannon silently stuck at not-ready
// rather than failing loudly. Files under `public/` are served from the app's
// own origin by the SPA catch-all, which `'self'` covers.
//
// Each file draws its shape in solid black: colorizing rewrites every pixel's
// color and keeps only the alpha channel, which is where the shape lives.
const SPRITES: string[] = [
  '/confetti/square.svg',
  '/confetti/circle.svg',
  '/confetti/ribbon.svg',
  '/confetti/triangle.svg',
];

export default SPRITES;
