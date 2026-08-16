import { useEffect, useRef, useState } from 'react';

/**
 * Auto-refresh hook that increments a counter on a fixed interval.
 *
 * Include the returned `refreshKey` in a data-fetching useEffect's dependency
 * array to re-trigger the fetch on each tick. The interval is paused while the
 * document is hidden (tab switched away) to avoid wasted requests, and resumes
 * immediately when the tab becomes visible again — so switching back to the
 * dashboard always shows fresh data without waiting for the next tick.
 *
 * @param intervalMs Polling interval in milliseconds (default 30000).
 * @returns A monotonically increasing counter. Add it to your effect deps.
 */
export function useAutoRefresh(intervalMs: number = 30000): number {
  const [refreshKey, setRefreshKey] = useState(0);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const start = () => {
      if (intervalRef.current) return;
      intervalRef.current = setInterval(() => {
        setRefreshKey((k) => k + 1);
      }, intervalMs);
    };

    const stop = () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };

    const onVisibilityChange = () => {
      if (document.hidden) {
        stop();
      } else {
        stop();
        setRefreshKey((k) => k + 1); // immediate refresh on tab focus
        start();
      }
    };

    if (!document.hidden) {
      start();
    }

    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      stop();
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [intervalMs]);

  return refreshKey;
}
