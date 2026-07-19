import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { CostTrendChart } from '../components/billing/CostTrendChart';

vi.mock('../services/billingApi', () => ({
  billingApi: {
    getCostTrend: vi.fn(),
  },
}));

import { billingApi } from '../services/billingApi';

const TREND_DATA = [
  { period: '2026-04', dimension_value: 'Virtual Machines', total_cost: 3000, currency: 'USD' },
  { period: '2026-04', dimension_value: 'Storage', total_cost: 500, currency: 'USD' },
  { period: '2026-05', dimension_value: 'Virtual Machines', total_cost: 3500, currency: 'USD' },
  { period: '2026-05', dimension_value: 'Storage', total_cost: 600, currency: 'USD' },
  { period: '2026-06', dimension_value: 'Virtual Machines', total_cost: 4000, currency: 'USD' },
  { period: '2026-06', dimension_value: 'Storage', total_cost: 700, currency: 'USD' },
  { period: '2026-06', dimension_value: 'Network', total_cost: 100, currency: 'USD' },
];

describe('CostTrendChart', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test('shows loading state initially', () => {
    (billingApi.getCostTrend as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    render(<CostTrendChart />);
    expect(screen.getByText(/Loading/i)).toBeInTheDocument();
  });

  test('renders SVG chart with title and legend after data loads', async () => {
    (billingApi.getCostTrend as ReturnType<typeof vi.fn>).mockResolvedValue(TREND_DATA);
    render(<CostTrendChart title="Monthly Cost Trend" />);
    await waitFor(() => expect(screen.getByText('Monthly Cost Trend')).toBeInTheDocument());
    // SVG element should be present
    expect(document.querySelector('svg')).toBeInTheDocument();
    // Legend entries for top services
    expect(screen.getByText('Virtual Machines')).toBeInTheDocument();
    expect(screen.getByText('Storage')).toBeInTheDocument();
  });

  test('shows empty state when no data', async () => {
    (billingApi.getCostTrend as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    render(<CostTrendChart />);
    await waitFor(() => expect(screen.getByText(/No cost trend data/i)).toBeInTheDocument());
  });

  test('shows error state on fetch failure', async () => {
    (billingApi.getCostTrend as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('API error'));
    render(<CostTrendChart />);
    await waitFor(() => expect(screen.getByText(/API error/i)).toBeInTheDocument());
  });

  test('calls API with months and dimension params', async () => {
    (billingApi.getCostTrend as ReturnType<typeof vi.fn>).mockResolvedValue(TREND_DATA);
    render(<CostTrendChart months={6} dimension="ResourceGroup" />);
    await waitFor(() => expect(billingApi.getCostTrend).toHaveBeenCalledWith(6, 'ResourceGroup'));
  });
});
