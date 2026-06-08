import { describe, expect, test, vi, afterEach } from 'vitest';
import { computeDateRange } from '../components/FilterBar';

describe('computeDateRange', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  test('uses Sunday-based weekly windows that match the dashboard scripts', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-06-07T12:00:00.000Z'));

    expect(computeDateRange('week', 1)).toEqual({
      start_date: '2026-05-31',
      end_date: '2026-06-06',
    });
    expect(computeDateRange('week', 2)).toEqual({
      start_date: '2026-05-24',
      end_date: '2026-05-30',
    });
  });
});
