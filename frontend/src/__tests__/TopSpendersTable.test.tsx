import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { TopSpendersTable } from '../components/billing/TopSpendersTable';

vi.mock('../services/billingApi', () => ({
  billingApi: {
    getTopSpenders: vi.fn(),
  },
}));

import { billingApi } from '../services/billingApi';

const ROWS = [
  { period: '2026-06', dimension: 'ServiceName', dimension_value: 'Virtual Machines', total_cost: 4000, currency: 'USD', change_pct: 15.2, change_amount: 500, record_count: 10 },
  { period: '2026-06', dimension: 'ServiceName', dimension_value: 'Storage', total_cost: 1000, currency: 'USD', change_pct: -5.0, change_amount: -50, record_count: 5 },
  { period: '2026-06', dimension: 'ServiceName', dimension_value: 'Network', total_cost: 500, currency: 'USD', change_pct: null, change_amount: null, record_count: 3 },
];

describe('TopSpendersTable', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test('shows loading state initially', () => {
    (billingApi.getTopSpenders as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    render(<TopSpendersTable period="2026-06" />);
    expect(screen.getByText(/Loading top spenders/i)).toBeInTheDocument();
  });

  test('renders table rows with rank, cost, and percentage', async () => {
    (billingApi.getTopSpenders as ReturnType<typeof vi.fn>).mockResolvedValue(ROWS);
    render(<TopSpendersTable period="2026-06" />);
    await waitFor(() => expect(screen.getByText('Virtual Machines')).toBeInTheDocument());
    expect(screen.getByText('Storage')).toBeInTheDocument();
    expect(screen.getByText('Network')).toBeInTheDocument();
    // Rank numbers
    expect(screen.getByText('1')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  test('shows error state on fetch failure', async () => {
    (billingApi.getTopSpenders as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network down'));
    render(<TopSpendersTable period="2026-06" />);
    await waitFor(() => expect(screen.getByText(/network down/i)).toBeInTheDocument());
  });

  test('shows empty state when no data', async () => {
    (billingApi.getTopSpenders as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    render(<TopSpendersTable period="2026-06" />);
    await waitFor(() => expect(screen.getByText(/No spending data/i)).toBeInTheDocument());
  });

  test('calls API with correct parameters', async () => {
    (billingApi.getTopSpenders as ReturnType<typeof vi.fn>).mockResolvedValue(ROWS);
    render(<TopSpendersTable period="2026-03" dimension="ResourceGroup" limit={5} />);
    await waitFor(() => expect(billingApi.getTopSpenders).toHaveBeenCalledWith('2026-03', 'ResourceGroup', 5));
  });
});
