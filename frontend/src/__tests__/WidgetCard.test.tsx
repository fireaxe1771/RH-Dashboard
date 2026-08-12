import { describe, expect, test, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { WidgetCard } from '../components/WidgetCard';
import { api } from '../services/api';

vi.mock('../services/api', () => ({
  api: {
    runSqlQuery: vi.fn(),
  },
}));

// Mock the export utilities so we can assert they are called without
// triggering real browser downloads in the test environment.
const exportToCsvMock = vi.fn();
const exportToExcelMock = vi.fn();
vi.mock('../utils/export', () => ({
  exportToCsv: (...args: unknown[]) => exportToCsvMock(...args),
  exportToExcel: (...args: unknown[]) => exportToExcelMock(...args),
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

  test('table widget renders rows and CSV/Excel export buttons', async () => {
    const tableData = {
      columns: ['Department', 'Drafts'],
      rows: [
        { Department: 'Metro Fire Dept', Drafts: 42 },
        { Department: 'Rural Fire Co', Drafts: 17 },
      ],
    };
    vi.mocked(api.runSqlQuery).mockResolvedValueOnce(tableData);

    render(
      <WidgetCard
        widget={{
          id: 'top-depts',
          title: 'Top Fire Departments by Drafts (Period)',
          type: 'table',
          sql_query: 'SELECT 1',
          layout: { x: 0, y: 0, w: 12, h: 10 },
          config: { xAxisKey: 'Department', yAxisKeys: ['Drafts'], colors: [] },
        }}
        filters={{}}
      />
    );

    // Wait for the data rows to render
    expect(await screen.findByText('Metro Fire Dept')).toBeInTheDocument();
    expect(screen.getByText('Rural Fire Co')).toBeInTheDocument();

    // Export buttons should be present
    expect(screen.getByText('CSV')).toBeInTheDocument();
    expect(screen.getByText('Excel')).toBeInTheDocument();

    // Clicking CSV should call the export utility with the widget data
    fireEvent.click(screen.getByText('CSV'));
    expect(exportToCsvMock).toHaveBeenCalledWith(
      'Top Fire Departments by Drafts (Period)',
      tableData.columns,
      tableData.rows,
    );

    // Clicking Excel should call the export utility with the widget data
    fireEvent.click(screen.getByText('Excel'));
    expect(exportToExcelMock).toHaveBeenCalledWith(
      'Top Fire Departments by Drafts (Period)',
      tableData.columns,
      tableData.rows,
    );
  });
});
