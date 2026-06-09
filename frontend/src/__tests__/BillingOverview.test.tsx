import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { BillingOverview } from '../components/billing/BillingOverview';

vi.mock('../services/billingApi', () => ({
  billingApi: {
    getCostSummary: vi.fn(),
    getBudgets: vi.fn(),
    getAdvisorSummary: vi.fn(),
    getCostTrend: vi.fn(),
  },
}));

import { billingApi } from '../services/billingApi';

describe('BillingOverview', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (billingApi.getCostTrend as ReturnType<typeof vi.fn>).mockResolvedValue([]);
  });

  test('shows loading state initially', () => {
    (billingApi.getCostSummary as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    (billingApi.getBudgets as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    (billingApi.getAdvisorSummary as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    render(<BillingOverview />);
    expect(screen.getByText(/Loading cost overview/i)).toBeInTheDocument();
  });

  test('renders KPI cards with fetched data', async () => {
    (billingApi.getCostSummary as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        { period: '2026-06', dimension: 'ServiceName', dimension_value: 'Virtual Machines', total_cost: 4200, currency: 'USD', change_pct: 12.5, change_amount: 100, record_count: 3 },
      ],
      total: 5000,
      currency: 'USD',
      period: '2026-06',
    });
    (billingApi.getBudgets as ReturnType<typeof vi.fn>).mockResolvedValue([
      { budget_id: 'b1', budget_name: 'Prod Budget', scope: 'sub', amount: 6000, current_spend: 5000, forecast_spend: 5800, utilization_pct: 83.3, time_grain: 'Monthly' },
    ]);
    (billingApi.getAdvisorSummary as ReturnType<typeof vi.fn>).mockResolvedValue({
      total_recommendations: 8,
      cost_recommendations: 3,
      total_monthly_savings: 750,
      currency: 'USD',
      by_impact: { High: 2, Medium: 4, Low: 2 },
    });

    render(<BillingOverview />);

    await waitFor(() => expect(screen.getByText('MTD Spend')).toBeInTheDocument());
    expect(screen.getByText('Virtual Machines')).toBeInTheDocument();
    expect(screen.getByText('Prod Budget')).toBeInTheDocument();
    expect(screen.getByText(/cost recommendation/i)).toBeInTheDocument();
  });

  test('shows error state on failure', async () => {
    (billingApi.getCostSummary as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('boom'));
    (billingApi.getBudgets as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (billingApi.getAdvisorSummary as ReturnType<typeof vi.fn>).mockResolvedValue({
      total_recommendations: 0, cost_recommendations: 0, total_monthly_savings: 0, currency: 'USD', by_impact: {},
    });

    render(<BillingOverview />);
    await waitFor(() => expect(screen.getByText(/boom/i)).toBeInTheDocument());
  });
});
