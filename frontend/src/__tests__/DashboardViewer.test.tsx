import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { DashboardViewer } from '../components/DashboardViewer';
import { Dashboard } from '../services/api';

// Mock the API service
vi.mock('../services/api', () => ({
  api: {
    getServerDate: vi.fn(),
    getDrillDownData: vi.fn(),
  },
  setAuthToken: vi.fn(),
  getAuthToken: vi.fn(() => null),
}));

// Mock FilterBar to avoid complex child rendering
vi.mock('../components/FilterBar', () => ({
  FilterBar: ({ filters }: { filters: any }) => (
    <div data-testid="filter-bar">FilterBar: {filters.range_type}</div>
  ),
  computeDateRange: vi.fn(() => ({ start_date: '2026-06-01', end_date: '2026-06-07' })),
  DashboardFilters: {} as any,
}));

// Mock WidgetCard to avoid SQL query execution
vi.mock('../components/WidgetCard', () => ({
  WidgetCard: ({ widget }: { widget: any }) => (
    <div data-testid={`widget-${widget.id}`}>{widget.title}</div>
  ),
}));

import { api } from '../services/api';

const MOCK_DASHBOARD: Dashboard = {
  _id: 'dash-1',
  name: 'Test Dashboard',
  description: 'A test dashboard',
  widgets: [
    { id: 'w1', title: 'Total Claims', type: 'stat', layout: { x: 0, y: 0, w: 4, h: 2 }, config: {} },
    { id: 'w2', title: 'Claims by Type', type: 'bar', layout: { x: 4, y: 0, w: 8, h: 4 }, config: {} },
  ],
};

describe('DashboardViewer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test('shows loading state while fetching server date', () => {
    (api.getServerDate as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    render(<DashboardViewer dashboard={MOCK_DASHBOARD} />);
    expect(screen.getByText(/Loading server date/i)).toBeInTheDocument();
  });

  test('renders widgets after server date loads', async () => {
    (api.getServerDate as ReturnType<typeof vi.fn>).mockResolvedValue('2026-06-07');
    render(<DashboardViewer dashboard={MOCK_DASHBOARD} />);
    await waitFor(() => expect(screen.getByTestId('widget-w1')).toBeInTheDocument());
    expect(screen.getByTestId('widget-w2')).toBeInTheDocument();
    expect(screen.getByText('Total Claims')).toBeInTheDocument();
    expect(screen.getByText('Claims by Type')).toBeInTheDocument();
  });

  test('falls back to browser date when server date fetch fails', async () => {
    (api.getServerDate as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network error'));
    render(<DashboardViewer dashboard={MOCK_DASHBOARD} />);
    // Should still render widgets despite the error
    await waitFor(() => expect(screen.getByTestId('widget-w1')).toBeInTheDocument());
  });

  test('renders FilterBar component', async () => {
    (api.getServerDate as ReturnType<typeof vi.fn>).mockResolvedValue('2026-06-07');
    render(<DashboardViewer dashboard={MOCK_DASHBOARD} />);
    await waitFor(() => expect(screen.getByTestId('filter-bar')).toBeInTheDocument());
  });

  test('renders dashboard with no widgets gracefully', async () => {
    (api.getServerDate as ReturnType<typeof vi.fn>).mockResolvedValue('2026-06-07');
    const emptyDashboard = { ...MOCK_DASHBOARD, widgets: [] };
    render(<DashboardViewer dashboard={emptyDashboard} />);
    await waitFor(() => expect(screen.getByTestId('filter-bar')).toBeInTheDocument());
    // No widget elements should be present
    expect(screen.queryByTestId('widget-w1')).not.toBeInTheDocument();
  });
});
