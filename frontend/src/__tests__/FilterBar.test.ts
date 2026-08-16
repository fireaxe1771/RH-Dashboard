import { describe, expect, test, vi, beforeEach } from 'vitest';
import * as FilterBarModule from '../components/FilterBar';
import { DEFAULT_RANGE_TYPE } from '../components/FilterBar';

vi.mock('../services/fetchWrapper', () => ({
  createApiFetch: () => vi.fn(),
}));

describe('date range ownership', () => {
  /**
   * Period arithmetic must exist in exactly one place: the backend
   * (target_db.compute_date_range, served by GET /api/date-range). A second
   * TypeScript implementation used to live in FilterBar and drifted from the
   * Python one — the same "current week ends today" bug had to be fixed twice.
   * This guard fails if that helper is reintroduced.
   */
  test('FilterBar does not export a local date-range calculator', () => {
    expect(FilterBarModule).not.toHaveProperty('computeDateRange');
  });

  test('the default range type is defined once and is week', () => {
    expect(DEFAULT_RANGE_TYPE).toBe('week');
  });
});

describe('api.getDateRange', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  test('requests the backend endpoint with range_type and periods_back', async () => {
    const fetchJson = vi.fn().mockResolvedValue({
      server_date: '2026-06-07',
      start_date: '2026-05-31',
      end_date: '2026-06-06',
    });
    vi.doMock('../services/fetchWrapper', () => ({
      createApiFetch: () => fetchJson,
    }));

    const { api } = await import('../services/api');
    const result = await api.getDateRange('week', 1);

    expect(fetchJson).toHaveBeenCalledWith(
      '/api/date-range?range_type=week&periods_back=1',
    );
    expect(result).toEqual({
      server_date: '2026-06-07',
      start_date: '2026-05-31',
      end_date: '2026-06-06',
    });
  });

  test('defaults periods_back to 0 (the current period)', async () => {
    const fetchJson = vi.fn().mockResolvedValue({
      server_date: '2026-08-16',
      start_date: '2026-08-16',
      end_date: '2026-08-22',
    });
    vi.doMock('../services/fetchWrapper', () => ({
      createApiFetch: () => fetchJson,
    }));

    const { api } = await import('../services/api');
    await api.getDateRange('week');

    expect(fetchJson).toHaveBeenCalledWith(
      '/api/date-range?range_type=week&periods_back=0',
    );
  });
});
