import { useEffect, useState, type ReactNode } from 'react';
import './ViewportGuard.css';

const MIN_WIDTH = 768;
const MEDIA_QUERY = `(min-width: ${MIN_WIDTH}px)`;

/**
 * Short-circuits the whole app when the viewport is narrower than MIN_WIDTH.
 * The tool is desktop-first; rather than silently degrading at phone widths,
 * we show a polite "this is designed for wider screens" panel. Resizing up
 * into the supported range swaps to the real app without a reload.
 */
export function ViewportGuard({ children }: { children: ReactNode }) {
  const [isWide, setIsWide] = useState(() => {
    if (typeof window === 'undefined') return true;
    return window.matchMedia(MEDIA_QUERY).matches;
  });

  useEffect(() => {
    const mql = window.matchMedia(MEDIA_QUERY);
    const handler = (e: MediaQueryListEvent) => setIsWide(e.matches);
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, []);

  if (isWide) return <>{children}</>;
  return <ViewportDenial />;
}

function ViewportDenial() {
  const [width, setWidth] = useState(() =>
    typeof window === 'undefined' ? 0 : window.innerWidth,
  );
  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  return (
    <main className="viewport-denial" role="main">
      <div className="viewport-denial-card">
        <h1 className="viewport-denial-brand">VC Portfolio</h1>
        <p className="viewport-denial-primary">
          This workspace is designed for screens {MIN_WIDTH}px and wider.
        </p>
        <p className="viewport-denial-secondary">
          Try landscape, open a wider window, or switch to a laptop.
        </p>
        <p className="viewport-denial-current">
          Current width: <strong>{width}px</strong>
        </p>
      </div>
    </main>
  );
}
