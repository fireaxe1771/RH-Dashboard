import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import React from 'react';
import { AiOutcomesDashboard } from '../components/ai/AiOutcomesDashboard';

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
  aiAnalyticsApi: {
    getOutcomeSummary: vi.fn(),
    getOutcomeFunnel: vi.fn(),
    getRejectionReasons: vi.fn(),
    getDepartmentOutcomes: vi.fn(),
    getBillabilityStats: vi.fn(),
    getInvoiceCohort: vi.fn(),
    getInvoiceTrace: vi.fn(),
  },
}));

vi.mock('../services/api', () => ({
  api: {
    getServerDate: vi.fn().mockResolvedValue('2026-08-13'),
    getDateRange: vi.fn().mockResolvedValue({ server_date: '2026-08-13', start_date: '2026-08-09', end_date: '2026-08-15' }),
  },
}));

import { aiAnalyticsApi } from '../services/aiAnalyticsApi';

const SUMMARY = {
  total_ai_invoices: 100,
  did_not_qualify: 12,
  released: 60,
  cancelled_rejected: 20,
  pending: 15,
  unknown: 5,
  terminal_count: 80,
  business_release_rate: 75.0,
  rejection_rate: 25.0,
  ai_completed: 90,
  ai_failed: 5,
  ai_not_enabled: 5,
  writeback_success: 55,
  writeback_not_saved: 5,
  confidence_count: 95,
  avg_confidence: 88,
  source_status: { recoveryhub_sql: 'available', recoveryhub_ai_mongo: 'available' },
  data_complete: true,
};

const FUNNEL = [
  { stage: 'Reached Ready to Invoice Insurance', count: 100, description: 'Claims in AIInvoiceProcessRHTemp' },
  { stage: 'Eligible for AI processing', count: 90, description: 'Department has a qualifying AI fee tile' },
  { stage: 'Step 1: level & category evaluated', count: 85, description: 'ai_line_items record exists — step 1 ran' },
  {
    stage: 'AI processing reached final result',
    count: 80,
    description: 'Evaluated claims with a terminal AI result',
    breakdown: [
      { label: 'Accepted and saved to RecoveryHub', count: 55, result_filter: 'saved_to_recoveryhub' },
      { label: 'AI output rejected', count: 15, result_filter: 'ai_output_rejected' },
      { label: 'Execution failed', count: 5, result_filter: 'execution_failed' },
      { label: 'Not required', count: 5, result_filter: 'not_required' },
    ],
    dropoff_breakdown: [
      { label: 'Stuck beyond 30 minutes', count: 3, result_filter: 'stuck' },
      { label: 'Result unknown', count: 2, result_filter: 'unknown' },
    ],
  },
  { stage: 'Line items saved to RH', count: 70, description: 'AI line items saved to RecoveryHub; this is not the same as invoice release.' },
  { stage: 'Released', count: 60, description: 'Invoice to Insurance - Released' },
];

const REJECTION_REASONS = [
  {
    normalized_category: 'documentation',
    count: 10,
    percent: 50.0,
    raw_reason_breakdown: [
      { raw_reason: 'Missing docs', count: 7 },
      { raw_reason: 'Incomplete form', count: 3 },
    ],
  },
  {
    normalized_category: 'eligibility',
    count: 10,
    percent: 50.0,
    raw_reason_breakdown: [{ raw_reason: 'Not eligible', count: 10 }],
  },
];

const DEPARTMENTS = [
  {
    department_id: 5,
    department_name: 'Metro Fire',
    state: 'CA',
    volume: 50,
    released: 30,
    rejected: 10,
    pending: 10,
    release_rate: 60.0,
    ai_completion_rate: 90.0,
    writeback_not_saved_rate: 5.0,
    avg_confidence: 85,
    retry_count: 2,
    human_intervention_count: 1,
  },
];

const BILLABILITY = {
  ai_records: 100,
  billability_determined: 80,
  billability_undetermined: 20,
  billable: 70,
  not_billable: 10,
  billing_category_distribution: { billable: 70, not_billable: 10 },
};

const COHORT = {
  invoices: [],
  total_count: 0,
  page: 1,
  page_size: 50,
  source_status: {},
  data_complete: true,
};

describe('AiOutcomesDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (aiAnalyticsApi.getOutcomeSummary as ReturnType<typeof vi.fn>).mockResolvedValue(SUMMARY);
    (aiAnalyticsApi.getOutcomeFunnel as ReturnType<typeof vi.fn>).mockResolvedValue(FUNNEL);
    (aiAnalyticsApi.getRejectionReasons as ReturnType<typeof vi.fn>).mockResolvedValue(REJECTION_REASONS);
    (aiAnalyticsApi.getDepartmentOutcomes as ReturnType<typeof vi.fn>).mockResolvedValue(DEPARTMENTS);
    (aiAnalyticsApi.getBillabilityStats as ReturnType<typeof vi.fn>).mockResolvedValue(BILLABILITY);
    (aiAnalyticsApi.getInvoiceCohort as ReturnType<typeof vi.fn>).mockResolvedValue(COHORT);
  });

  test('shows loading state initially', () => {
    (aiAnalyticsApi.getOutcomeSummary as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    render(<AiOutcomesDashboard />);
    expect(screen.getByText(/Loading AI outcomes/i)).toBeInTheDocument();
  });

  test('renders KPI cards with summary values', async () => {
    render(<AiOutcomesDashboard />);
    await waitFor(() => expect(screen.getByText('AI Cohort Claims')).toBeInTheDocument());
    expect(screen.getByText('Did Not Qualify')).toBeInTheDocument();
    expect(screen.getByText('No qualifying AI fee tile')).toBeInTheDocument();
    expect(screen.getByText('Terminal Release Rate')).toBeInTheDocument();
    expect(screen.getByText('Terminal Rejection Rate')).toBeInTheDocument();
    expect(screen.getByText('Writeback Success')).toBeInTheDocument();
    expect(screen.getByText('Avg AI Confidence')).toBeInTheDocument();
    // "Pending" and "AI Completed" appear as both KPI labels and funnel stages,
    // so use getAllByText to verify they render at least once.
    expect(screen.getAllByText('Pending').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('AI Completed').length).toBeGreaterThanOrEqual(1);
  });

  test('renders funnel with stage names and counts', async () => {
    render(<AiOutcomesDashboard />);
    await waitFor(() => expect(screen.getByText('AI Invoice Pipeline Funnel')).toBeInTheDocument());
    expect(screen.getByText('Reached Ready to Invoice Insurance')).toBeInTheDocument();
    expect(screen.getByText('Line items saved to RH')).toBeInTheDocument();
    // "AI Completed" and "Released" also appear as KPI labels — verify they exist
    expect(screen.getAllByText('AI Completed').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Released').length).toBeGreaterThanOrEqual(1);
  });

  test('renders final-result breakdown and not-yet-final dropoff rows', async () => {
    render(<AiOutcomesDashboard />);
    await waitFor(() =>
      expect(screen.getByText('AI processing reached final result')).toBeInTheDocument()
    );
    expect(screen.getByText('Accepted and saved to RecoveryHub')).toBeInTheDocument();
    expect(screen.getByText('Not yet at final result')).toBeInTheDocument();
    expect(screen.getByText('Stuck beyond 30 minutes')).toBeInTheDocument();
    expect(screen.getByText('Result unknown')).toBeInTheDocument();
  });

  test('renders AI Processing Exceptions panel derived from funnel', async () => {
    render(<AiOutcomesDashboard />);
    await waitFor(() => expect(screen.getByText('AI Processing Exceptions')).toBeInTheDocument());
    expect(screen.getByText('Stuck processing')).toBeInTheDocument();
    // 'Execution failed' also appears in the funnel stage-4 breakdown
    expect(screen.getAllByText('Execution failed').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('Unknown result')).toBeInTheDocument();
    // Counts come from the funnel breakdown/dropoff (stuck=3, rejected=15,
    // failed=5, unknown=2)
    expect(screen.getByText('Stuck processing').parentElement).toHaveTextContent('3');
  });

  test('clicking an exception card filters the cohort grid via ai_result', async () => {
    render(<AiOutcomesDashboard />);
    await waitFor(() => expect(screen.getByText('Stuck processing')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Stuck processing'));
    await waitFor(() =>
      expect(aiAnalyticsApi.getInvoiceCohort).toHaveBeenCalledWith(
        expect.objectContaining({ ai_result: 'stuck' })
      )
    );
    // Funnel/KPI aggregate calls must NOT carry the result filter
    for (const call of (aiAnalyticsApi.getOutcomeFunnel as ReturnType<typeof vi.fn>).mock.calls) {
      expect(call[0].ai_result).toBeUndefined();
    }
  });

  test('funnel breakdown row click sets the same result filter', async () => {
    render(<AiOutcomesDashboard />);
    await waitFor(() =>
      expect(screen.getAllByText('AI output rejected').length).toBeGreaterThanOrEqual(2)
    );
    // First match is the funnel breakdown row (funnel renders before the panel)
    fireEvent.click(screen.getAllByText('AI output rejected')[0]);
    await waitFor(() =>
      expect(aiAnalyticsApi.getInvoiceCohort).toHaveBeenCalledWith(
        expect.objectContaining({ ai_result: 'ai_output_rejected' })
      )
    );
  });

  test('clear filter resets the cohort grid filter', async () => {
    render(<AiOutcomesDashboard />);
    await waitFor(() => expect(screen.getByText('Stuck processing')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Stuck processing'));
    await waitFor(() => expect(screen.getAllByText('Clear filter').length).toBeGreaterThanOrEqual(1));
    fireEvent.click(screen.getByText('Clear filter'));
    await waitFor(() =>
      expect(aiAnalyticsApi.getInvoiceCohort).toHaveBeenLastCalledWith(
        expect.not.objectContaining({ ai_result: 'stuck' })
      )
    );
  });

  test('renders rejection reasons with normalized categories', async () => {
    render(<AiOutcomesDashboard />);
    await waitFor(() => expect(screen.getByText('Rejection Reasons (Normalized)')).toBeInTheDocument());
    // normalized_category has underscores replaced with spaces
    expect(screen.getByText('documentation')).toBeInTheDocument();
    expect(screen.getByText('eligibility')).toBeInTheDocument();
    // Raw reason breakdown (only shown when > 1 breakdown entry)
    expect(screen.getByText(/Missing docs/)).toBeInTheDocument();
  });

  test('renders department comparison table', async () => {
    render(<AiOutcomesDashboard />);
    await waitFor(() => expect(screen.getByText('Top Departments by AI Invoice Volume')).toBeInTheDocument());
    expect(screen.getByText('Metro Fire')).toBeInTheDocument();
    expect(screen.getByText('CA')).toBeInTheDocument();
  });

  test('renders billability section', async () => {
    render(<AiOutcomesDashboard />);
    await waitFor(() => expect(screen.getByText('Incident / Billability Evaluation')).toBeInTheDocument());
    expect(screen.getByText('AI Records')).toBeInTheDocument();
    expect(screen.getByText('Billability Determined')).toBeInTheDocument();
    expect(screen.getByText('Billable')).toBeInTheDocument();
  });

  test('shows error state on fetch failure', async () => {
    (aiAnalyticsApi.getOutcomeSummary as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('outcomes fetch failed')
    );
    render(<AiOutcomesDashboard />);
    await waitFor(() => expect(screen.getByText(/outcomes fetch failed/i)).toBeInTheDocument());
  });

  test('shows data incomplete warning when data_complete is false', async () => {
    (aiAnalyticsApi.getOutcomeSummary as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...SUMMARY,
      data_complete: false,
      source_status: { recoveryhub_sql: 'unavailable', recoveryhub_ai_mongo: 'available' },
    });
    render(<AiOutcomesDashboard />);
    await waitFor(() => expect(screen.getByText(/Data may be incomplete/i)).toBeInTheDocument());
    expect(screen.getByText(/recoveryhub_sql=unavailable/i)).toBeInTheDocument();
  });

  test('shows "Billing Not Enabled" KPI when ai_not_enabled > 0', async () => {
    render(<AiOutcomesDashboard />);
    await waitFor(() => expect(screen.getByText('Billing Not Enabled')).toBeInTheDocument());
  });

  test('hides "Billing Not Enabled" KPI when ai_not_enabled is 0', async () => {
    (aiAnalyticsApi.getOutcomeSummary as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...SUMMARY,
      ai_not_enabled: 0,
    });
    render(<AiOutcomesDashboard />);
    await waitFor(() => expect(screen.getByText('AI Cohort Claims')).toBeInTheDocument());
    expect(screen.queryByText('Billing Not Enabled')).not.toBeInTheDocument();
  });

  test('renders filter bar with range type selector', async () => {
    render(<AiOutcomesDashboard />);
    await waitFor(() => expect(screen.getByText('Range')).toBeInTheDocument());
    expect(screen.getByText('Period')).toBeInTheDocument();
  });
});
