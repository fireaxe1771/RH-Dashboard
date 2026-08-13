import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import React from 'react';
import { AiInvoiceTrace } from '../components/ai/AiInvoiceTrace';
import { AiInvoiceTrace as AiInvoiceTraceType } from '../services/aiAnalyticsApi';

vi.mock('../services/aiAnalyticsApi', () => ({
  aiAnalyticsApi: {
    getInvoiceTrace: vi.fn(),
  },
}));

import { aiAnalyticsApi } from '../services/aiAnalyticsApi';

const TRACE: AiInvoiceTraceType = {
  claim_id: 1001,
  invoice_number: 'INV-1001',
  run_number: '42',
  department_id: 5,
  department_name: 'Metro Fire',
  claim_created_at: '2026-01-10T08:00:00Z',
  alarm_received: null,
  call_cleared: null,
  recoveryhub_claim_status: 'submitted',
  business_outcome: 'released',
  ai_inv_process_status: 2,
  business_status_updated_at: '2026-01-12T10:00:00Z',
  process_logs: [
    { id: 1, log_text: 'AI processing started', user_id: 1, user_type_id: 2, created_date: '2026-01-10T09:00:00Z' },
  ],
  cancellation_reason: null,
  cancellation_description: null,
  cancellation_date: null,
  business_user_id: 10,
  ai_record_state: 'active',
  claim_processing_status: 'completed',
  agent_exec_status: 'success',
  inserted_at: '2026-01-10T09:00:00Z',
  updated_at: '2026-01-12T10:00:00Z',
  completed_at: '2026-01-11T12:00:00Z',
  billing_category: 'billable',
  incident_duration_in_minutes: 30,
  confidence_level: 95,
  review_msg: 'All line items verified.',
  line_items_save_to_rh_status: true,
  invoice_total: 1500.0,
  processing_time_seconds: 12.5,
  retry_count: 0,
  conversation_id: 'conv-1',
  thread_id_is_billable: null,
  thread_id: 't1',
  retry_thread_id: null,
  conversations: [
    {
      conversation_id: 'c1',
      agent: 'LineItemAgent',
      status: 'completed',
      created_at: '2026-01-10T09:30:00Z',
      processing_stage: 'extraction',
      request_type: 'process',
      execution_time_seconds: 8.2,
      input_data: { claim: 1001 },
      incident_json: null,
      results: { items: 5 },
      output_data: { saved: true },
    },
  ],
  ai_line_items: [
    { item: 'Oxygen', description: 'O2 therapy', quantity: 3, rate: 50.0, line_item_total: 150.0, resources: [] },
  ],
  final_line_items: [
    { claim_service_id: 1, item: 'Oxygen', rate: 50.0, quantity: 3, description: 'O2 therapy', resources: [] },
  ],
  comparison: {
    ai_original_amount: 150.0,
    final_rh_amount: 150.0,
    difference: 0,
    ai_only_items: [],
    rh_only_items: [],
    quantity_changes: [],
    rate_changes: [],
  },
  raw_ai_record: null,
  source_status: {},
  data_complete: true,
};

describe('AiInvoiceTrace', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test('shows loading state initially', () => {
    (aiAnalyticsApi.getInvoiceTrace as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    render(<AiInvoiceTrace claimId={1001} onBack={vi.fn()} />);
    expect(screen.getByText(/Loading trace for claim 1001/i)).toBeInTheDocument();
  });

  test('renders trace header with claim ID and department', async () => {
    (aiAnalyticsApi.getInvoiceTrace as ReturnType<typeof vi.fn>).mockResolvedValue(TRACE);
    render(<AiInvoiceTrace claimId={1001} onBack={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/Claim 1001/i)).toBeInTheDocument());
    expect(screen.getByText(/Metro Fire/i)).toBeInTheDocument();
    // "Run #42" appears in both the header and the timeline event detail
    expect(screen.getAllByText(/Run #42/i).length).toBeGreaterThanOrEqual(1);
  });

  test('renders summary fields (outcome, AI status, confidence, writeback)', async () => {
    (aiAnalyticsApi.getInvoiceTrace as ReturnType<typeof vi.fn>).mockResolvedValue(TRACE);
    render(<AiInvoiceTrace claimId={1001} onBack={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('Business Outcome')).toBeInTheDocument());
    expect(screen.getByText('AI Status')).toBeInTheDocument();
    expect(screen.getByText('Confidence')).toBeInTheDocument();
    expect(screen.getByText('Writeback')).toBeInTheDocument();
    // Outcome badge text
    expect(screen.getByText('released')).toBeInTheDocument();
    // Writeback success
    expect(screen.getByText('Success')).toBeInTheDocument();
  });

  test('back button calls onBack', async () => {
    (aiAnalyticsApi.getInvoiceTrace as ReturnType<typeof vi.fn>).mockResolvedValue(TRACE);
    const onBack = vi.fn();
    render(<AiInvoiceTrace claimId={1001} onBack={onBack} />);
    await waitFor(() => expect(screen.getByText(/Back to Cohort/i)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/Back to Cohort/i));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  test('renders timeline events', async () => {
    (aiAnalyticsApi.getInvoiceTrace as ReturnType<typeof vi.fn>).mockResolvedValue(TRACE);
    render(<AiInvoiceTrace claimId={1001} onBack={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('Timeline')).toBeInTheDocument());
    expect(screen.getByText('Claim Created')).toBeInTheDocument();
    expect(screen.getByText('AI Processing Started')).toBeInTheDocument();
    expect(screen.getByText('AI Processing Completed')).toBeInTheDocument();
    expect(screen.getByText('Invoice Released')).toBeInTheDocument();
    // Process log entry
    expect(screen.getByText('AI processing started')).toBeInTheDocument();
  });

  test('renders conversation viewer with agent name', async () => {
    (aiAnalyticsApi.getInvoiceTrace as ReturnType<typeof vi.fn>).mockResolvedValue(TRACE);
    render(<AiInvoiceTrace claimId={1001} onBack={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('LineItemAgent')).toBeInTheDocument());
    // "completed" appears as both AI status and conversation status
    expect(screen.getAllByText('completed').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('extraction')).toBeInTheDocument();
  });

  test('expanding conversation shows input/results JSON', async () => {
    (aiAnalyticsApi.getInvoiceTrace as ReturnType<typeof vi.fn>).mockResolvedValue(TRACE);
    render(<AiInvoiceTrace claimId={1001} onBack={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('LineItemAgent')).toBeInTheDocument());
    // The first conversation is expanded by default (initial state = 0),
    // so the input/results/output labels should already be visible.
    expect(screen.getByText('Input Data')).toBeInTheDocument();
    expect(screen.getByText('Results')).toBeInTheDocument();
    expect(screen.getByText('Output Data')).toBeInTheDocument();
  });

  test('renders line item comparison tables', async () => {
    (aiAnalyticsApi.getInvoiceTrace as ReturnType<typeof vi.fn>).mockResolvedValue(TRACE);
    render(<AiInvoiceTrace claimId={1001} onBack={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('Line Item Comparison')).toBeInTheDocument());
    expect(screen.getByText('AI-Generated Line Items')).toBeInTheDocument();
    expect(screen.getByText('Final RH Line Items')).toBeInTheDocument();
    // "Oxygen" appears in both AI and Final RH line item tables
    expect(screen.getAllByText('Oxygen').length).toBeGreaterThanOrEqual(1);
  });

  test('renders comparison summary (AI Original, Final RH, Difference)', async () => {
    (aiAnalyticsApi.getInvoiceTrace as ReturnType<typeof vi.fn>).mockResolvedValue(TRACE);
    render(<AiInvoiceTrace claimId={1001} onBack={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('AI Original')).toBeInTheDocument());
    expect(screen.getByText('Final RH')).toBeInTheDocument();
    expect(screen.getByText('Difference')).toBeInTheDocument();
  });

  test('shows error state on fetch failure', async () => {
    (aiAnalyticsApi.getInvoiceTrace as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('trace fetch failed')
    );
    render(<AiInvoiceTrace claimId={1001} onBack={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/trace fetch failed/i)).toBeInTheDocument());
  });

  test('renders cancellation reason when present', async () => {
    const cancelledTrace = {
      ...TRACE,
      business_outcome: 'cancelled_rejected',
      cancellation_reason: 'Duplicate claim',
      cancellation_description: 'Already submitted under claim #999',
      cancellation_date: '2026-01-13T10:00:00Z',
    };
    (aiAnalyticsApi.getInvoiceTrace as ReturnType<typeof vi.fn>).mockResolvedValue(cancelledTrace);
    render(<AiInvoiceTrace claimId={1001} onBack={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('Cancellation Reason')).toBeInTheDocument());
    // "Duplicate claim" appears in both the reason div and the description context
    expect(screen.getAllByText('Duplicate claim').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Already submitted under claim #999')).toBeInTheDocument();
  });
});
