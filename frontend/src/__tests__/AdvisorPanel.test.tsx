import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import React from 'react';
import { AdvisorPanel } from '../components/billing/AdvisorPanel';

vi.mock('../services/billingApi', () => ({
  billingApi: {
    getAdvisorSummary: vi.fn(),
    getAdvisorRecommendations: vi.fn(),
  },
}));

import { billingApi } from '../services/billingApi';

const REC = {
  recommendation_id: 'rec-1',
  category: 'Cost',
  impact: 'High' as const,
  impacted_value: 'vm-prod-01',
  resource_group: 'rg-prod',
  problem_description: 'This VM is underutilized and can be resized to save money on compute costs every month.',
  solution_description: 'Resize to Standard_D2s_v5.',
  estimated_monthly_savings: 320,
  savings_currency: 'USD',
  current_sku: 'Standard_D8s_v5',
  recommended_sku: 'Standard_D2s_v5',
  last_updated: '2026-06-01',
  status: 'Active',
};

describe('AdvisorPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (billingApi.getAdvisorSummary as ReturnType<typeof vi.fn>).mockResolvedValue({
      total_recommendations: 1,
      cost_recommendations: 1,
      total_monthly_savings: 320,
      currency: 'USD',
      by_impact: { High: 1, Medium: 0, Low: 0 },
    });
    (billingApi.getAdvisorRecommendations as ReturnType<typeof vi.fn>).mockResolvedValue([REC]);
  });

  test('renders recommendation cards', async () => {
    render(<AdvisorPanel />);
    await waitFor(() => expect(screen.getByText('vm-prod-01')).toBeInTheDocument());
    expect(screen.getAllByText(/320/).length).toBeGreaterThan(0);
    expect(screen.getByText(/potential savings/i)).toBeInTheDocument();
  });

  test('clicking a category filter refetches with that category', async () => {
    render(<AdvisorPanel />);
    await waitFor(() => expect(screen.getByText('vm-prod-01')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Security'));
    await waitFor(() =>
      expect(billingApi.getAdvisorRecommendations).toHaveBeenLastCalledWith('Security', undefined),
    );
  });

  test('View Details expands the solution text', async () => {
    render(<AdvisorPanel />);
    await waitFor(() => expect(screen.getByText('vm-prod-01')).toBeInTheDocument());

    fireEvent.click(screen.getByText(/View Details/i));
    await waitFor(() => expect(screen.getByText(/Recommended action/i)).toBeInTheDocument());
  });
});
