import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { BudgetsPanel } from '../components/billing/BudgetsPanel';

vi.mock('../services/billingApi', () => ({
  billingApi: {
    getBudgets: vi.fn(),
    getAlerts: vi.fn(),
  },
}));

import { billingApi } from '../services/billingApi';

const BUDGETS = [
  { budget_id: 'b1', budget_name: 'Prod', scope: 'sub-1', amount: 10000, current_spend: 8000, forecast_spend: 9000, utilization_pct: 80, time_grain: 'Monthly', currency: 'USD' },
  { budget_id: 'b2', budget_name: 'Dev', scope: 'sub-2', amount: 5000, current_spend: 2000, forecast_spend: 2500, utilization_pct: 40, time_grain: 'Monthly', currency: 'USD' },
];

const ALERTS = [
  { alert_id: 'a1', alert_name: 'Budget Exceeded', alert_type: 'Threshold', status: 'Exceeded', description: 'Prod budget exceeded', budget_name: 'Prod', current_spend: 10100, threshold: 10000, currency: 'USD', creation_time: '2026-06-01' },
  { alert_id: 'a2', alert_name: '', alert_type: 'Forecast', status: 'Active', description: 'Forecast approaching threshold', budget_name: 'Dev', current_spend: 4500, threshold: 5000, currency: 'USD', creation_time: '2026-06-02' },
];

describe('BudgetsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test('shows loading state initially', () => {
    (billingApi.getBudgets as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    (billingApi.getAlerts as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    render(<BudgetsPanel />);
    expect(screen.getByText(/Loading budgets/i)).toBeInTheDocument();
  });

  test('renders sorted budget cards (highest utilization first)', async () => {
    (billingApi.getBudgets as ReturnType<typeof vi.fn>).mockResolvedValue(BUDGETS);
    (billingApi.getAlerts as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    render(<BudgetsPanel />);
    await waitFor(() => expect(screen.getByText('Prod')).toBeInTheDocument());
    expect(screen.getByText('Dev')).toBeInTheDocument();
  });

  test('renders alerts with name or type fallback', async () => {
    (billingApi.getBudgets as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (billingApi.getAlerts as ReturnType<typeof vi.fn>).mockResolvedValue(ALERTS);
    render(<BudgetsPanel />);
    await waitFor(() => expect(screen.getByText('Budget Exceeded')).toBeInTheDocument());
    // alert_name is empty → falls back to alert_type
    expect(screen.getByText('Forecast')).toBeInTheDocument();
  });

  test('shows empty states when no budgets and no alerts', async () => {
    (billingApi.getBudgets as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (billingApi.getAlerts as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    render(<BudgetsPanel />);
    await waitFor(() => expect(screen.getByText(/No budgets configured/i)).toBeInTheDocument());
    expect(screen.getByText(/No active cost alerts/i)).toBeInTheDocument();
  });

  test('shows error state on fetch failure', async () => {
    (billingApi.getBudgets as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('fetch failed'));
    (billingApi.getAlerts as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    render(<BudgetsPanel />);
    await waitFor(() => expect(screen.getByText(/fetch failed/i)).toBeInTheDocument());
  });
});
