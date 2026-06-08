import { describe, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { WidgetCard } from '../components/WidgetCard';
import { api } from '../services/api';

vi.mock('../services/api', () => ({
  api: {
    runSqlQuery: vi.fn(),
  },
}));

describe('WidgetCard Component', () => {
  test('renders comparison stat values when query returns two numeric fields', async () => {
    vi.mocked(api.runSqlQuery).mockResolvedValueOnce({
      columns: ['CurrentPeriod', 'PreviousPeriod'],
      rows: [{ CurrentPeriod: 12, PreviousPeriod: 9 }],
    });

    render(
      <WidgetCard
        widget={{
          id: 'draft-compare',
          title: 'Draft Claims Created This Period',
          type: 'stat',
          sql_query: 'SELECT 1',
          layout: { x: 0, y: 0, w: 4, h: 3 },
          config: { xAxisKey: '', yAxisKeys: [], colors: [] },
        }}
        filters={{}}
      />
    );

    expect(await screen.findByText('12')).toBeInTheDocument();
    expect(screen.getByText('9')).toBeInTheDocument();
    expect(screen.getByText('Previous Period')).toBeInTheDocument();
  });
});
