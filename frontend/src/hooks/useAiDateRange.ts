import { useEffect, useState } from 'react';
import { api } from '../services/api';
import { computeDateRange, RangeType } from '../components/FilterBar';

/**
 * Shared date-range initialization for AI dashboards.
 *
 * Every AI dashboard (Adoption, Outcomes, Diagnostics) needs the same setup:
 * fetch the database server date, compute an initial date window, and expose
 * it for both the filter bar and the data-fetching effect. This hook
 * centralizes that logic so the default range type is defined in exactly one
 * place — change it here and every dashboard picks it up.
 *
 * @param defaultRangeType  The initial range type (default 'week').
 * @param defaultPeriodsBack The initial periods-back value (default 0 = current).
 * @returns serverDate, startDate, endDate, dateReady, and setters for the
 *          start/end dates so the filter bar can update them.
 */
export function useAiDateRange(
  defaultRangeType: RangeType = 'week',
  defaultPeriodsBack: number = 0,
) {
  const [serverDate, setServerDate] = useState<string | undefined>(undefined);
  const [dateReady, setDateReady] = useState(false);
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');

  useEffect(() => {
    let active = true;
    api.getServerDate()
      .then((dateStr) => {
        if (!active) return;
        setServerDate(dateStr);
        const dates = computeDateRange(defaultRangeType, defaultPeriodsBack, dateStr);
        setStartDate(dates.start_date);
        setEndDate(dates.end_date);
        setDateReady(true);
      })
      .catch(() => {
        if (!active) return;
        const dates = computeDateRange(defaultRangeType, defaultPeriodsBack);
        setStartDate(dates.start_date);
        setEndDate(dates.end_date);
        setDateReady(true);
      });
    return () => { active = false; };
  }, [defaultRangeType, defaultPeriodsBack]);

  return {
    serverDate,
    startDate,
    endDate,
    dateReady,
    setStartDate,
    setEndDate,
    defaultRangeType,
  };
}
