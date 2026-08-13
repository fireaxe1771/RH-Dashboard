import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import React from 'react';
import { AiInvoiceCohortGrid } from '../components/ai/AiInvoiceCohortGrid';
import { AiAnalyticsFilters } from '../services/aiAnalyticsApi';

vi.mock('../services/aiAnalyticsApi', () => ({
  aiAnalyticsApi: {
    getInvoiceCohort: vi.fn(),
  },
}));

vi.mock('../utils/export', () => ({
  exportToCsv: vi.fn(),
}));

import { aiAnalyticsApi } from '../services/aiAnalyticsApi';
import { exportToCsv } from '../utils/export';

const FILTERS: AiAnalyticsFilters = { start_date: '2026-01-01', end_date: '2026-01-31' };

const INVOICES = [
  {
    claim_id: 1001,
    invoice_number: 'INV-1001',
    department_id: 5,
    department_name: 'Metro Fire',
    run_number: '42',
    claim_created_at: '2026-01-10T08:00:00Z',
    ai_business_updated_at: '2026-01-12T10:00:00Z',
    business_outcome: 'released',
    raw_rejection_reason: null,
    raw_rejection_description: null,
    normalized_rejection_category: null,
    ai_processing_status: 'completed',
    agent_execution_status: 'success',
    is_billable: true,
    billing_category: 'billable',
    confidence: 95,
    writeback_status: 'success',
    retry_count: 0,
    thread_id: 't1',
    ai_record_state: 'active',
    business_record_state: 'active',
    invoice_total: 1500.0,
    amount_invoiced: 1500.0,
    processing_time_seconds: 12.5,
  },
  {
    claim_id: 1002,
    invoice_number: null,
    department_id: 6,
    department_name: null,
    run_number: null,
    claim_created_at: null,
    ai_business_updated_at: null,
    business_outcome: 'cancelled_rejected',
    raw_rejection_reason: 'Missing documentation',
    raw_rejection_description: null,
    normalized_rejection_category: 'documentation',
    ai_processing_status: 'completed',
    agent_execution_status: 'success',
    is_billable: false,
    billing_category: null,
    confidence: null,
    writeback_status: 'not_required',
    retry_count: 1,
    thread_id: null,
    ai_record_state: 'active',
    business_record_state: 'cancelled',
    invoice_total: null,
    amount_invoiced: null,
    processing_time_seconds: null,
  },
];

const COHORT_RESPONSE = {
  invoices: INVOICES,
  total_count: 2,
  page: 1,
  page_size: 50,
  source_status: {},
  data_complete: true,
};

describe('AiInvoiceCohortGrid', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test('shows loading state initially', () => {
    (aiAnalyticsApi.getInvoiceCohort as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    render(<AiInvoiceCohortGrid filters={FILTERS} />);
    expect(screen.getByText(/Loading invoice cohort/i)).toBeInTheDocument();
  });

  test('renders invoice rows with claim IDs and departments', async () => {
    (aiAnalyticsApi.getInvoiceCohort as ReturnType<typeof vi.fn>).mockResolvedValue(COHORT_RESPONSE);
    render(<AiInvoiceCohortGrid filters={FILTERS} />);
    await waitFor(() => expect(screen.getByText('1001')).toBeInTheDocument());
    expect(screen.getByText('Metro Fire')).toBeInTheDocument();
    expect(screen.getByText('1002')).toBeInTheDocument();
    // Department name null → em dash fallback
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  test('shows empty state when no invoices match', async () => {
    (aiAnalyticsApi.getInvoiceCohort as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...COHORT_RESPONSE,
      invoices: [],
      total_count: 0,
    });
    render(<AiInvoiceCohortGrid filters={FILTERS} />);
    await waitFor(() => expect(screen.getByText(/No invoices match/i)).toBeInTheDocument());
  });

  test('shows error state on fetch failure', async () => {
    (aiAnalyticsApi.getInvoiceCohort as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('cohort fetch failed')
    );
    render(<AiInvoiceCohortGrid filters={FILTERS} />);
    await waitFor(() => expect(screen.getByText(/cohort fetch failed/i)).toBeInTheDocument());
  });

  test('export CSV button calls exportToCsv with mapped rows', async () => {
    (aiAnalyticsApi.getInvoiceCohort as ReturnType<typeof vi.fn>).mockResolvedValue(COHORT_RESPONSE);
    render(<AiInvoiceCohortGrid filters={FILTERS} />);
    await waitFor(() => expect(screen.getByText('Export CSV')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Export CSV'));
    expect(exportToCsv).toHaveBeenCalledTimes(1);
    const [, columns, rows] = (exportToCsv as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(columns).toContain('Claim ID');
    expect(columns).toContain('Business Outcome');
    expect(rows).toHaveLength(2);
    expect(rows[0]['Claim ID']).toBe(1001);
    expect(rows[0]['Business Outcome']).toBe('released');
    expect(rows[1]['Rejection Reason']).toBe('Missing documentation');
  });

  test('row click calls onRowClick with claim_id', async () => {
    (aiAnalyticsApi.getInvoiceCohort as ReturnType<typeof vi.fn>).mockResolvedValue(COHORT_RESPONSE);
    const onRowClick = vi.fn();
    render(<AiInvoiceCohortGrid filters={FILTERS} onRowClick={onRowClick} />);
    await waitFor(() => expect(screen.getByText('1001')).toBeInTheDocument());
    fireEvent.click(screen.getByText('1001'));
    expect(onRowClick).toHaveBeenCalledWith(1001);
  });

  test('pagination shows page info', async () => {
    (aiAnalyticsApi.getInvoiceCohort as ReturnType<typeof vi.fn>).mockResolvedValue(COHORT_RESPONSE);
    render(<AiInvoiceCohortGrid filters={FILTERS} />);
    await waitFor(() => expect(screen.getByText(/Page 1 of/i)).toBeInTheDocument());
    expect(screen.getByText(/Page 1 of 1/i)).toBeInTheDocument();
  });

  test('passes page and page_size in API call', async () => {
    (aiAnalyticsApi.getInvoiceCohort as ReturnType<typeof vi.fn>).mockResolvedValue(COHORT_RESPONSE);
    render(<AiInvoiceCohortGrid filters={FILTERS} />);
    await waitFor(() => expect(aiAnalyticsApi.getInvoiceCohort).toHaveBeenCalled());
    const callArg = (aiAnalyticsApi.getInvoiceCohort as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(callArg.page).toBe(1);
    expect(callArg.page_size).toBe(50);
    expect(callArg.start_date).toBe('2026-01-01');
  });
});
