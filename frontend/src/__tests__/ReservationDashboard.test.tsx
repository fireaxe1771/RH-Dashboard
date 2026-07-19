import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import React from 'react';
import { ReservationDashboard } from '../components/billing/ReservationDashboard';

vi.mock('../services/billingApi', () => ({
  billingApi: {
    getReservationRecommendations: vi.fn(),
  },
}));

import { billingApi } from '../services/billingApi';

const RECS = [
  { subscription_id: 'sub-1', sku_name: 'Standard_D2s_v5', resource_type: 'virtualMachines', scope: 'Shared', term: 'P1Y', look_back_period: 'Last30Days', location: 'eastus', recommended_quantity: 3, total_cost_with_no_ri: 12000, total_cost_with_ri: 8000, net_savings: 4000, currency: 'USD' },
  { subscription_id: 'sub-2', sku_name: 'Standard_F4s_v5', resource_type: 'virtualMachines', scope: 'Shared', term: 'P3Y', look_back_period: 'Last30Days', location: 'westus', recommended_quantity: 2, total_cost_with_no_ri: 9000, total_cost_with_ri: 5000, net_savings: 4000, currency: 'USD' },
];

describe('ReservationDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test('shows loading state initially', () => {
    (billingApi.getReservationRecommendations as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    render(<ReservationDashboard />);
    expect(screen.getByText(/Loading reservation/i)).toBeInTheDocument();
  });

  test('renders P1Y recommendations by default', async () => {
    (billingApi.getReservationRecommendations as ReturnType<typeof vi.fn>).mockResolvedValue(RECS);
    render(<ReservationDashboard />);
    await waitFor(() => expect(screen.getByText('Standard_D2s_v5')).toBeInTheDocument());
    expect(screen.queryByText('Standard_F4s_v5')).not.toBeInTheDocument();
  });

  test('switching to 3-Year tab shows P3Y recommendations', async () => {
    (billingApi.getReservationRecommendations as ReturnType<typeof vi.fn>).mockResolvedValue(RECS);
    render(<ReservationDashboard />);
    await waitFor(() => expect(screen.getByText('Standard_D2s_v5')).toBeInTheDocument());

    fireEvent.click(screen.getByText('3-Year'));
    await waitFor(() => expect(screen.getByText('Standard_F4s_v5')).toBeInTheDocument());
  });

  test('shows empty state when no recommendations for selected term', async () => {
    (billingApi.getReservationRecommendations as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    render(<ReservationDashboard />);
    await waitFor(() => expect(screen.getByText(/No 1-Year reservation/i)).toBeInTheDocument());
  });

  test('shows error state on fetch failure', async () => {
    (billingApi.getReservationRecommendations as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('failed'));
    render(<ReservationDashboard />);
    await waitFor(() => expect(screen.getByText(/failed/i)).toBeInTheDocument());
  });

  test('shows payback period when savings and upfront cost are available', async () => {
    (billingApi.getReservationRecommendations as ReturnType<typeof vi.fn>).mockResolvedValue(RECS);
    render(<ReservationDashboard />);
    await waitFor(() => expect(screen.getByText(/Payback/i)).toBeInTheDocument());
    // 8000 upfront / 4000 monthly savings = 2 months
    expect(screen.getByText(/~2 mo/)).toBeInTheDocument();
  });

  test('shows future release notice card', async () => {
    (billingApi.getReservationRecommendations as ReturnType<typeof vi.fn>).mockResolvedValue(RECS);
    render(<ReservationDashboard />);
    await waitFor(() => expect(screen.getByText(/Utilization details coming/i)).toBeInTheDocument());
  });
});
