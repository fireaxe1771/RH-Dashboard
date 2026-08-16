import { useEffect, useState } from 'react';
import { api } from '../services/api';
import { RangeType, DEFAULT_RANGE_TYPE } from '../components/FilterBar';

/**
 * Shared date-range initialization for AI dashboards.
 *
 * Every AI dashboard (Adoption, Outcomes, Diagnostics) needs the same setup:
 * resolve the initial date window and expose it to both the filter bar and the
 * data-fetching effect.
 *
 * The period arithmetic itself is NOT implemented here. It lives in the backend
 * (`target_db.compute_date_range`, served by `GET /api/date-range`) so that the
 * frontend and the SQL layer can never disagree about what "current week"
 * means. This hook only decides *which* period to ask for and holds the answer.
 *
 * @param defaultRangeType   The initial range type. Defaults to DEFAULT_RANGE_TYPE.
 * @param defaultPeriodsBack The initial periods-back value (0 = current).
 * @returns serverDate, startDate, endDate, dateReady, rangeError, and setters so
 *          the filter bar can push user-selected dates back in.
 */
export function useAiDateRange(
  defaultRangeType: RangeType = DEFAULT_RANGE_TYPE,
  defaultPeriodsBack: number = 0,
) {
  const [serverDate, setServerDate] = useState<string | undefined>(undefined);
  const [dateReady, setDateReady] = useState(false);
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [rangeError, setRangeError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api.getDateRange(defaultRangeType, defaultPeriodsBack)
      .then((range) => {
        if (!active) return;
        setServerDate(range.server_date);
        setStartDate(range.start_date);
        setEndDate(range.end_date);
        setRangeError(null);
        setDateReady(true);
      })
      .catch((err: unknown) => {
        if (!active) return;
        // Deliberately no local fallback: recomputing the range in the browser
        // is exactly what let the frontend and backend definitions drift.
        // Report the failure instead of querying with an unresolved range.
        const msg = err instanceof Error ? err.message : String(err);
        setRangeError(`Could not resolve the date range: ${msg}`);
      });
    return () => { active = false; };
  }, [defaultRangeType, defaultPeriodsBack]);

  return {
    serverDate,
    startDate,
    endDate,
    dateReady,
    rangeError,
    setStartDate,
    setEndDate,
    defaultRangeType,
  };
}
