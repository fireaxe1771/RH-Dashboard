import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import React from 'react';
import { AICostAnalyst } from '../components/billing/AICostAnalyst';

vi.mock('../services/billingApi', () => ({
  billingApi: {
    aiQuery: vi.fn(),
  },
}));

import { billingApi } from '../services/billingApi';

describe('AICostAnalyst', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  test('renders example question chips on empty state', () => {
    render(<AICostAnalyst />);
    expect(screen.getByText(/top spending services this month/i)).toBeInTheDocument();
    expect(screen.getByText(/save the most money/i)).toBeInTheDocument();
  });

  test('submitting a question shows loading then the answer with sources', async () => {
    let resolveQuery: (v: unknown) => void = () => {};
    (billingApi.aiQuery as ReturnType<typeof vi.fn>).mockReturnValue(
      new Promise((resolve) => {
        resolveQuery = resolve;
      }),
    );

    render(<AICostAnalyst />);
    const input = screen.getByLabelText(/Ask about your Azure costs/i);
    fireEvent.change(input, { target: { value: 'Why is my bill high?' } });
    fireEvent.click(screen.getByText('Send'));

    // User message + loading indicator
    expect(screen.getByText('Why is my bill high?')).toBeInTheDocument();
    expect(screen.getByText(/Analyzing/i)).toBeInTheDocument();

    resolveQuery({
      answer: 'Your VMs are the main driver.',
      sources: [{ document_type: 'top_spenders', period: '2026-06', dimension_value: 'VMs', total_cost: 100, score: 0.92 }],
      model: 'gpt-4o-mini',
      question: 'Why is my bill high?',
    });

    await waitFor(() => expect(screen.getByText(/main driver/i)).toBeInTheDocument());
    expect(screen.getByText(/Sources \(1\)/i)).toBeInTheDocument();
  });

  test('clicking an example chip submits that question', async () => {
    (billingApi.aiQuery as ReturnType<typeof vi.fn>).mockResolvedValue({
      answer: 'Here are your top services.',
      sources: [],
      model: 'gpt-4o-mini',
      question: 'x',
    });

    render(<AICostAnalyst />);
    fireEvent.click(screen.getByText(/top spending services this month/i));
    await waitFor(() => expect(billingApi.aiQuery).toHaveBeenCalledTimes(1));
  });
});
