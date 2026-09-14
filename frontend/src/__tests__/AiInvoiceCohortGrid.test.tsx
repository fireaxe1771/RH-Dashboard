import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import React from 'react';
import { AiInvoiceCohortGrid } from '../components/ai/AiInvoiceCohortGrid';
import { AiAnalyticsFilters } from '../services/aiAnalyticsApi';

vi.mock('../services/aiAnalyticsApi', () => ({
  AI_RESULT_LABELS: {
    saved_to_recoveryhub: 'Saved to RH',
    ai_output_rejected: 'AI Output Rejected',
    execution_failed: 'Execution Failed',
    stuck: 'Stuck',
    in_progress: 'In Progress',
    not_required: 'Not Required',
    unknown: 'Unknown',
  },
  AI_STUCK_FALLBACK_REASON:
    'No source error was recorded. The workflow exceeded the inactivity threshold; investigate in FireRecovery_AI.',
  AI_EXECUTION_FAILED_FALLBACK_REASON:
    'The AI execution failed, but no source error reason was recorded; inspect the agent conversation history.',
  aiResultBadgeStyle: () => ({}),
  formatProcessingAge: (s: number | null | undefined) => {
    if (s === null || s === undefined) return '—';
    if (s < 60) return `${Math.round(s)}s`;
    const m = s / 60;
    if (m < 60) return `${Math.round(m)}m`;
    const h = m / 60;
    if (h < 24) return `${Math.round(h)}h`;
    return `${Math.round(h / 24)}d`;
  },
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
    ai_result: 'saved_to_recoveryhub',
    review_message: null,
    ai_inserted_at: '2026-01-10T09:00:00Z',
    ai_updated_at: '2026-01-12T10:00:00Z',
    ai_completed_at: '2026-01-11T12:00:00Z',
    ai_line_item_count: 5,
    is_stuck: false,
    processing_age_seconds: 120,
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
    ai_result: 'ai_output_rejected',
    review_message: 'AI output rejected: wrong billing level identified',
    ai_inserted_at: null,
    ai_updated_at: null,
    ai_completed_at: null,
    ai_line_item_count: 0,
    is_stuck: false,
    processing_age_seconds: null,
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
    expect(screen.getByText(/Invoice Cohort \(2 AI cohort claims\)/)).toBeInTheDocument();
    expect(screen.getByText('1002')).toBeInTheDocument();
    // Department name null → em dash fallback
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  test('uses human labels for each final AI result', async () => {
    (aiAnalyticsApi.getInvoiceCohort as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...COHORT_RESPONSE,
      invoices: [
        { ...INVOICES[0], ai_result: 'saved_to_recoveryhub' },
        { ...INVOICES[0], claim_id: 1003, ai_result: 'ai_output_rejected', review_message: 'bad' },
        { ...INVOICES[0], claim_id: 1004, ai_result: 'execution_failed' },
        { ...INVOICES[0], claim_id: 1005, ai_result: 'stuck', review_message: null, processing_age_seconds: 5400 },
        { ...INVOICES[0], claim_id: 1006, ai_result: 'in_progress', processing_age_seconds: 300 },
        { ...INVOICES[0], claim_id: 1007, ai_result: 'not_required' },
        { ...INVOICES[0], claim_id: 1008, ai_result: 'unknown' },
      ],
      total_count: 7,
    });
    render(<AiInvoiceCohortGrid filters={FILTERS} />);
    await waitFor(() => expect(screen.getByText('Saved to RH')).toBeInTheDocument());
    expect(screen.getByText('AI Output Rejected')).toBeInTheDocument();
    expect(screen.getByText('Execution Failed')).toBeInTheDocument();
    expect(screen.getByText('Stuck')).toBeInTheDocument();
    expect(screen.getByText('In Progress')).toBeInTheDocument();
    expect(screen.getByText('Not Required')).toBeInTheDocument();
    expect(screen.getByText('Unknown')).toBeInTheDocument();
  });

  test('shows stuck fallback reason and compact processing age', async () => {
    (aiAnalyticsApi.getInvoiceCohort as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...COHORT_RESPONSE,
      invoices: [
        { ...INVOICES[0], claim_id: 1005, ai_result: 'stuck', review_message: null, processing_age_seconds: 5400 },
      ],
      total_count: 1,
    });
    render(<AiInvoiceCohortGrid filters={FILTERS} />);
    await waitFor(() =>
      expect(
        screen.getByText(/No source error was recorded\. The workflow exceeded the inactivity threshold/)
      ).toBeInTheDocument()
    );
    expect(screen.getByText('2h')).toBeInTheDocument(); // 5400s → 2h (rounded 1.5h→2h)
  });

  test('rejected output shows reason preview with Show/Hide reason toggle', async () => {
    const longReason = `Rejected output: ${'detail '.repeat(20)}`;
    (aiAnalyticsApi.getInvoiceCohort as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...COHORT_RESPONSE,
      invoices: [
        { ...INVOICES[1], ai_result: 'ai_output_rejected', review_message: longReason },
      ],
      total_count: 1,
    });
    const onRowClick = vi.fn();
    render(<AiInvoiceCohortGrid filters={FILTERS} onRowClick={onRowClick} />);
    await waitFor(() => expect(screen.getByText('Show reason')).toBeInTheDocument());
    // The button sits inside the reason span; the truncated preview carries
    // an ellipsis and not the full text.
    const reasonCell = () =>
      screen.getByRole('button', { name: /reason/i }).parentElement!;
    expect(reasonCell().textContent).toContain('…');
    expect(reasonCell().textContent).not.toContain(longReason.trimEnd());
    fireEvent.click(screen.getByText('Show reason'));
    expect(reasonCell().textContent).toContain(longReason.trimEnd());
    // Expanding must not trigger row navigation
    expect(onRowClick).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('Hide reason'));
    expect(reasonCell().textContent).not.toContain(longReason.trimEnd());
  });

  test('shows active result filter with clear action', async () => {
    (aiAnalyticsApi.getInvoiceCohort as ReturnType<typeof vi.fn>).mockResolvedValue(COHORT_RESPONSE);
    const onClear = vi.fn();
    render(
      <AiInvoiceCohortGrid
        filters={{ ...FILTERS, ai_result: 'stuck' }}
        onClearResultFilter={onClear}
      />
    );
    await waitFor(() => expect(screen.getByText(/Showing final AI result/)).toBeInTheDocument());
    expect(screen.getByText('Stuck')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Clear'));
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  test('shows execution-failure fallback reason when no review message exists', async () => {
    (aiAnalyticsApi.getInvoiceCohort as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...COHORT_RESPONSE,
      invoices: [
        { ...INVOICES[0], claim_id: 1004, ai_result: 'execution_failed', review_message: null },
      ],
      total_count: 1,
    });
    render(<AiInvoiceCohortGrid filters={FILTERS} />);
    await waitFor(() =>
      expect(
        screen.getByText(/The AI execution failed, but no source error reason was recorded/)
      ).toBeInTheDocument()
    );
  });

  test('CSV uses execution-failure fallback reason', async () => {
    (aiAnalyticsApi.getInvoiceCohort as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...COHORT_RESPONSE,
      invoices: [
        { ...INVOICES[0], claim_id: 1004, ai_result: 'execution_failed', review_message: null },
      ],
      total_count: 1,
    });
    render(<AiInvoiceCohortGrid filters={FILTERS} />);
    await waitFor(() => expect(screen.getByText('Export CSV')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Export CSV'));
    const [, , rows] = (exportToCsv as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(rows[0]['AI Review Reason']).toBe(
      'The AI execution failed, but no source error reason was recorded; inspect the agent conversation history.'
    );
  });

  test('changing ai_result filter resets request to page 1', async () => {
    (aiAnalyticsApi.getInvoiceCohort as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...COHORT_RESPONSE,
      total_count: 120,
    });
    const { rerender } = render(<AiInvoiceCohortGrid filters={FILTERS} />);
    await waitFor(() => expect(screen.getByText(/Page 1 of 3/i)).toBeInTheDocument());
    // Navigate to page 2 — the icon-only Next button is the last button
    // in the pagination row at the bottom of the component.
    const buttons = screen.getAllByRole('button');
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => {
      const calls = (aiAnalyticsApi.getInvoiceCohort as ReturnType<typeof vi.fn>).mock.calls;
      expect(calls[calls.length - 1][0].page).toBe(2);
    });
    // Change the ai_result filter — request must restart at page 1
    rerender(<AiInvoiceCohortGrid filters={{ ...FILTERS, ai_result: 'stuck' }} />);
    await waitFor(() => {
      const calls = (aiAnalyticsApi.getInvoiceCohort as ReturnType<typeof vi.fn>).mock.calls;
      const last = calls[calls.length - 1][0];
      expect(last.page).toBe(1);
      expect(last.ai_result).toBe('stuck');
    });
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
    expect(columns).toContain('Final AI Result');
    expect(columns).toContain('AI Review Reason');
    expect(columns).toContain('AI Items');
    expect(columns).toContain('AI Last Updated');
    expect(columns).toContain('Processing Age Seconds');
    expect(rows).toHaveLength(2);
    expect(rows[0]['Claim ID']).toBe(1001);
    expect(rows[0]['Business Outcome']).toBe('released');
    expect(rows[0]['Final AI Result']).toBe('Saved to RH');
    expect(rows[0]['AI Items']).toBe(5);
    expect(rows[1]['AI Review Reason']).toBe('AI output rejected: wrong billing level identified');
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
