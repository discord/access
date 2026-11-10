import React from 'react';

/**
 * Renders an empty element that calls `onVisible` whenever it scrolls into
 * view. Place it at the bottom of a list to drive infinite scroll: wire
 * `onVisible` to fetch the next page (the parent should no-op when there's
 * nothing more to load or a fetch is already in flight).
 */
export const InfiniteScrollSentinel: React.FC<{onVisible: () => void; disabled?: boolean}> = ({
  onVisible,
  disabled = false,
}) => {
  const ref = React.useRef<HTMLDivElement>(null);
  // The observer calls whichever callback is current, so a parent passing a
  // fresh closure each render doesn't tear down and re-create the observer.
  const cb = React.useRef(onVisible);
  React.useLayoutEffect(() => {
    cb.current = onVisible;
  });

  React.useEffect(() => {
    if (disabled) {
      return;
    }
    const el = ref.current;
    if (!el) {
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        cb.current();
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [disabled]);

  return <div ref={ref} aria-hidden style={{height: 1}} />;
};
