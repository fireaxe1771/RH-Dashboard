import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { AiDiagnosticsDashboard } from '../components/ai/AiDiagnosticsDashboard';

vi.mock('../services/aiAnalyticsApi', () => ({
  aiAnalyticsApi: {
    getDiagnosticsSummary: vi.fn(),
    getConfidenceDistribution: vi.fn(),
    getStatusDistribution: vi.fn(),
    getRetryAnalysis: vi.fn(),
    getWritebackAnalysis: vi.fn(),
    getAgentStats: vi.fn(),
  },
}));

vi.mock('../services/api', () => ({
  api: {
    getServerDate: vi.fn().mockResolvedValue('2026-08-13'),
    getDateRange: vi.fn().mockResolvedValue({ server_date: '2026-08-13', start_date: '2026-08-09', end_date: '2026-08-15' }),
  },
}));

import { aiAnalyticsApi } from '../services/aiAnalyticsApi';

const DIAGNOSTICS_SUMMARY = {
  ai_runs: 100,
  completed: 90,
  errors: 5,
  retries: 10,
  retry_success: 7,
  low_confidence: 8,
  writeback_failures: 3,
  avg_duration: 12.5,
  p50_duration: 10.0,
  p90_duration: 20.0,
  p95_duration: 25.0,
  source_status: { recoveryhub_sql: 'available', recoveryhub_ai_mongo: 'available' },
  data_complete: true,
};

const CONFIDENCE_BUCKETS = [
  { bucket: '0-25%', count: 5, released: 1, rejected: 3, pending: 1, release_rate: 20.0 },
  { bucket: '25-50%', count: 10, released: 4, rejected: 4, pending: 2, release_rate: 40.0 },
  { bucket: '50-75%', count: 30, released: 20, rejected: 5, pending: 5, release_rate: 66.7 },
  { bucket: '75-100%', count: 55, released: 45, rejected: 5, pending: 5, release_rate: 81.8 },
];

const STATUS_DIST = [
  { dimension: 'ai_processing_status', value: 'completed', count: 90 },
  { dimension: 'ai_processing_status', value: 'failed', count: 5 },
  { dimension: 'ai_processing_status', value: 'pending', count: 5 },
];

const RETRY_ANALYSIS = {
  total_records: 100,
  records_with_retries: 10,
  retry_rate: 10.0,
  retry_success_rate: 70.0,
  retry_count_distribution: { '1': 7, '2': 3 },
  retried_outcome_distribution: { released: 5, cancelled_rejected: 3, pending: 2 },
};

const WRITEBACK_ANALYSIS = {
  total_records: 100,
  status_distribution: { success: 55, failed_or_not_saved: 5, not_required: 40 },
  failure_count: 5,
  failure_rate: 5.0,
  failure_by_processing_status: { completed: 3, failed: 2 },
};

const AGENT_STATS = [
  { agent: 'LineItemAgent', status: 'completed', processing_stage: 'extraction', request_type: 'process', count: 50, avg_execution_time: 8.2 },
  { agent: 'ReviewAgent', status: 'completed', processing_stage: 'review', request_type: 'process', count: 40, avg_execution_time: 5.1 },
];

describe('AiDiagnosticsDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (aiAnalyticsApi.getDiagnosticsSummary as ReturnType<typeof vi.fn>).mockResolvedValue(DIAGNOSTICS_SUMMARY);
    (aiAnalyticsApi.getConfidenceDistribution as ReturnType<typeof vi.fn>).mockResolvedValue(CONFIDENCE_BUCKETS);
    (aiAnalyticsApi.getStatusDistribution as ReturnType<typeof vi.fn>).mockResolvedValue(STATUS_DIST);
    (aiAnalyticsApi.getRetryAnalysis as ReturnType<typeof vi.fn>).mockResolvedValue(RETRY_ANALYSIS);
    (aiAnalyticsApi.getWritebackAnalysis as ReturnType<typeof vi.fn>).mockResolvedValue(WRITEBACK_ANALYSIS);
    (aiAnalyticsApi.getAgentStats as ReturnType<typeof vi.fn>).mockResolvedValue(AGENT_STATS);
  });

  test('shows loading state initially', () => {
    (aiAnalyticsApi.getDiagnosticsSummary as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    render(<AiDiagnosticsDashboard />);
    expect(screen.getByText(/Loading AI diagnostics/i)).toBeInTheDocument();
  });

  test('renders KPI cards with diagnostics values', async () => {
    render(<AiDiagnosticsDashboard />);
    await waitFor(() => expect(screen.getByText('AI Runs')).toBeInTheDocument());
    expect(screen.getByText('Completed')).toBeInTheDocument();
    expect(screen.getByText('Errors')).toBeInTheDocument();
    expect(screen.getByText('Retries')).toBeInTheDocument();
    expect(screen.getByText('Low Confidence (<50%)')).toBeInTheDocument();
    expect(screen.getByText('Writeback Failures')).toBeInTheDocument();
    expect(screen.getByText('Avg Duration')).toBeInTheDocument();
    expect(screen.getByText('P95 Duration')).toBeInTheDocument();
  });

  test('renders confidence calibration view', async () => {
    render(<AiDiagnosticsDashboard />);
    await waitFor(() => expect(screen.getByText('Confidence Calibration')).toBeInTheDocument());
    // Bucket labels are rendered as "Confidence {bucket}%"
    expect(screen.getByText(/Confidence 0-25%/i)).toBeInTheDocument();
    expect(screen.getByText(/Confidence 75-100%/i)).toBeInTheDocument();
  });

  test('renders status distribution view', async () => {
    render(<AiDiagnosticsDashboard />);
    await waitFor(() => expect(screen.getByText('AI Processing Status Distribution')).toBeInTheDocument());
    // "completed" appears in both status distribution and agent stats
    expect(screen.getAllByText('completed').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('failed').length).toBeGreaterThanOrEqual(1);
  });

  test('renders retry analysis view', async () => {
    render(<AiDiagnosticsDashboard />);
    await waitFor(() => expect(screen.getByText('Retry Analysis')).toBeInTheDocument());
    expect(screen.getByText('Retry Rate')).toBeInTheDocument();
    expect(screen.getByText('Retry Success')).toBeInTheDocument();
  });

  test('renders writeback analysis view', async () => {
    render(<AiDiagnosticsDashboard />);
    await waitFor(() => expect(screen.getByText('Writeback Status Analysis')).toBeInTheDocument());
    expect(screen.getByText('Failure Rate')).toBeInTheDocument();
  });

  test('renders agent stats table', async () => {
    render(<AiDiagnosticsDashboard />);
    await waitFor(() => expect(screen.getByText('LineItemAgent')).toBeInTheDocument());
    expect(screen.getByText('ReviewAgent')).toBeInTheDocument();
  });

  test('shows error state on fetch failure', async () => {
    (aiAnalyticsApi.getDiagnosticsSummary as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('diagnostics fetch failed')
    );
    render(<AiDiagnosticsDashboard />);
    await waitFor(() => expect(screen.getByText(/diagnostics fetch failed/i)).toBeInTheDocument());
  });

  test('shows data incomplete warning when data_complete is false', async () => {
    (aiAnalyticsApi.getDiagnosticsSummary as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...DIAGNOSTICS_SUMMARY,
      data_complete: false,
      source_status: { recoveryhub_sql: 'unavailable', recoveryhub_ai_mongo: 'available' },
    });
    render(<AiDiagnosticsDashboard />);
    await waitFor(() => expect(screen.getByText(/Data may be incomplete/i)).toBeInTheDocument());
    expect(screen.getByText(/recoveryhub_sql=unavailable/i)).toBeInTheDocument();
  });

  test('renders filter bar without business outcome filter', async () => {
    render(<AiDiagnosticsDashboard />);
    await waitFor(() => expect(screen.getByText('Range')).toBeInTheDocument());
    // showOutcomeFilter=false, so business outcome filter should not appear
    expect(screen.queryByText('Business Outcome')).not.toBeInTheDocument();
  });
});
