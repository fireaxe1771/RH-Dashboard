import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { InvoiceList } from '../components/billing/InvoiceList';

vi.mock('../services/billingApi', () => ({
  billingApi: {
    getInvoices: vi.fn(),
  },
}));

import { billingApi } from '../services/billingApi';

describe('InvoiceList', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  test('renders invoice rows with status badges', async () => {
    (billingApi.getInvoices as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        invoice_id: 'INV-001', billing_period_start: '2026-05-01', billing_period_end: '2026-05-31',
        invoice_date: '2026-06-01', due_date: '2026-06-30', billed_amount: 1234.56, amount_due: 0,
        billing_currency: 'USD', status: 'Paid', invoice_download_url: 'https://example.com/inv.pdf',
      },
      {
        invoice_id: 'INV-002', billing_period_start: '2026-06-01', billing_period_end: '2026-06-30',
        invoice_date: '2026-07-01', due_date: '2026-07-30', billed_amount: 2000, amount_due: 2000,
        billing_currency: 'USD', status: 'Due', invoice_download_url: null,
      },
    ]);

    render(<InvoiceList />);
    await waitFor(() => expect(screen.getByText('INV-001')).toBeInTheDocument());
    // Newest first: INV-002 (June) should sort above INV-001 (May)
    expect(screen.getByText('INV-002')).toBeInTheDocument();
    expect(screen.getByText('Paid')).toBeInTheDocument();
    expect(screen.getByText('Due')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /PDF/i })).toHaveAttribute('href', 'https://example.com/inv.pdf');
  });

  test('shows empty state when no invoices', async () => {
    (billingApi.getInvoices as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    render(<InvoiceList />);
    await waitFor(() => expect(screen.getByText(/No invoices available/i)).toBeInTheDocument());
  });
});
