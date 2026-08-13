import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import React from 'react';
import { AiAdoptionDashboard } from '../components/ai/AiAdoptionDashboard';

vi.mock('../services/aiAdoptionApi', () => ({
  aiAdoptionApi: {
    getDepartments: vi.fn(),
  },
}));

vi.mock('../services/api', () => ({
  api: {
    getServerDate: vi.fn().mockResolvedValue('2026-08-13'),
  },
}));

vi.mock('../utils/export', () => ({
  exportToCsv: vi.fn(),
  exportToExcel: vi.fn(),
}));

import { aiAdoptionApi } from '../services/aiAdoptionApi';
import { api } from '../services/api';
import { exportToCsv, exportToExcel } from '../utils/export';

const ADOPTION_RESPONSE = {
  period: { start_date: '2026-01-01', end_date: '2026-01-31' },
  ai_status_basis: 'current_configuration',
  summary: {
    active_departments: 100,
    departments_using_ai: 60,
    departments_not_using_ai: 30,
    departments_unknown: 10,
    total_drafts: 5000,
    ai_department_drafts: 3000,
    non_ai_department_drafts: 1500,
    unknown_department_drafts: 500,
    ai_coverage_percent: 60.0,
    remaining_opportunity_percent: 40.0,
  },
  departments: [
    {
      rank_overall: 1,
      department_id: '5',
      department_name: 'Metro Fire',
      state: 'CA',
      submitted_drafts: 500,
      percent_of_total_volume: 10.0,
      ai_status: 'using_ai',
      ai_mode: 'auto',
      qualifying_fee_count: 100,
      has_auto: true,
      has_queued: false,
      has_limited_auto: false,
    },
    {
      rank_overall: 2,
      department_id: '6',
      department_name: 'Rural Fire',
      state: 'TX',
      submitted_drafts: 300,
      percent_of_total_volume: 6.0,
      ai_status: 'not_using_ai',
      ai_mode: 'off',
      qualifying_fee_count: 0,
      has_auto: false,
      has_queued: false,
      has_limited_auto: false,
    },
  ],
};

describe('AiAdoptionDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test('shows loading state initially', () => {
    (aiAdoptionApi.getDepartments as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    render(<AiAdoptionDashboard />);
    expect(screen.getByText(/Loading AI adoption/i)).toBeInTheDocument();
  });

  test('renders KPI cards with summary values', async () => {
    (aiAdoptionApi.getDepartments as ReturnType<typeof vi.fn>).mockResolvedValue(ADOPTION_RESPONSE);
    render(<AiAdoptionDashboard />);
    await waitFor(() => expect(screen.getByText('Active Departments')).toBeInTheDocument());
    expect(screen.getByText('AI Coverage')).toBeInTheDocument();
    // "Using AI" and "Not Using AI" appear in both KPI cards and tabs
    expect(screen.getAllByText('Using AI').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Not Using AI').length).toBeGreaterThanOrEqual(1);
  });

  test('renders department table with rows', async () => {
    (aiAdoptionApi.getDepartments as ReturnType<typeof vi.fn>).mockResolvedValue(ADOPTION_RESPONSE);
    render(<AiAdoptionDashboard />);
    await waitFor(() => expect(screen.getByText('Metro Fire')).toBeInTheDocument());
    expect(screen.getByText('Rural Fire')).toBeInTheDocument();
    expect(screen.getByText('CA')).toBeInTheDocument();
    expect(screen.getByText('TX')).toBeInTheDocument();
  });

  test('renders status tabs (All, Using AI, Not Using AI)', async () => {
    (aiAdoptionApi.getDepartments as ReturnType<typeof vi.fn>).mockResolvedValue(ADOPTION_RESPONSE);
    render(<AiAdoptionDashboard />);
    await waitFor(() => expect(screen.getByText('All Departments')).toBeInTheDocument());
    // "Using AI" and "Not Using AI" appear in both tabs and KPI cards
    expect(screen.getAllByText('Using AI').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Not Using AI').length).toBeGreaterThanOrEqual(1);
  });

  test('export CSV button calls exportToCsv', async () => {
    (aiAdoptionApi.getDepartments as ReturnType<typeof vi.fn>).mockResolvedValue(ADOPTION_RESPONSE);
    render(<AiAdoptionDashboard />);
    await waitFor(() => expect(screen.getByText('CSV')).toBeInTheDocument());
    fireEvent.click(screen.getByText('CSV'));
    expect(exportToCsv).toHaveBeenCalledTimes(1);
  });

  test('export Excel button calls exportToExcel', async () => {
    (aiAdoptionApi.getDepartments as ReturnType<typeof vi.fn>).mockResolvedValue(ADOPTION_RESPONSE);
    render(<AiAdoptionDashboard />);
    await waitFor(() => expect(screen.getByText('Excel')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Excel'));
    expect(exportToExcel).toHaveBeenCalledTimes(1);
  });

  test('shows error state on fetch failure', async () => {
    (aiAdoptionApi.getDepartments as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('adoption fetch failed')
    );
    render(<AiAdoptionDashboard />);
    await waitFor(() => expect(screen.getByText(/adoption fetch failed/i)).toBeInTheDocument());
  });

  test('shows empty state when no departments match', async () => {
    (aiAdoptionApi.getDepartments as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...ADOPTION_RESPONSE,
      departments: [],
    });
    render(<AiAdoptionDashboard />);
    await waitFor(() => expect(screen.getByText(/No departments match/i)).toBeInTheDocument());
  });

  test('renders AI status basis explanation', async () => {
    (aiAdoptionApi.getDepartments as ReturnType<typeof vi.fn>).mockResolvedValue(ADOPTION_RESPONSE);
    render(<AiAdoptionDashboard />);
    await waitFor(() => expect(screen.getByText(/AI Status reflects current/i)).toBeInTheDocument());
  });
});
