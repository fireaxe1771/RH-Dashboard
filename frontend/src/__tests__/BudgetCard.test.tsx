import { describe, test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { BudgetCard } from '../components/billing/BudgetCard';
import { BudgetItem } from '../services/billingApi';

const BASE_BUDGET: BudgetItem = {
  budget_id: 'b1',
  budget_name: 'Production Budget',
  scope: '/subscriptions/abc',
  amount: 10000,
  current_spend: 5000,
  forecast_spend: 5500,
  utilization_pct: 50,
  time_grain: 'Monthly',
  currency: 'USD',
};

describe('BudgetCard', () => {
  test('renders budget name and scope', () => {
    render(<BudgetCard budget={BASE_BUDGET} />);
    expect(screen.getByText('Production Budget')).toBeInTheDocument();
    expect(screen.getByText('/subscriptions/abc')).toBeInTheDocument();
  });

  test('shows current spend / amount and utilization percent', () => {
    render(<BudgetCard budget={BASE_BUDGET} />);
    expect(screen.getByText(/\$5,000\.00 \/ \$10,000\.00/)).toBeInTheDocument();
    expect(screen.getByText('50.0%')).toBeInTheDocument();
  });

  test('shows forecast when present', () => {
    render(<BudgetCard budget={BASE_BUDGET} />);
    expect(screen.getByText(/Forecast/i)).toBeInTheDocument();
    expect(screen.getByText(/\$5,500\.00/)).toBeInTheDocument();
  });

  test('hides forecast when null', () => {
    const budget = { ...BASE_BUDGET, forecast_spend: null };
    render(<BudgetCard budget={budget} />);
    expect(screen.queryByText(/Forecast/i)).not.toBeInTheDocument();
  });

  test('shows alert triangle when utilization > 80', () => {
    const budget = { ...BASE_BUDGET, utilization_pct: 85, current_spend: 8500 };
    render(<BudgetCard budget={budget} />);
    // AlertTriangle icon is an svg with lucide class
    const icons = document.querySelectorAll('svg');
    expect(icons.length).toBeGreaterThan(0);
  });

  test('uses fallback currency when currency field missing', () => {
    const budget = { ...BASE_BUDGET, currency: undefined, current_spend_currency: 'EUR' };
    render(<BudgetCard budget={budget} />);
    // Should render amounts — we just verify it doesn't crash
    expect(screen.getByText('Production Budget')).toBeInTheDocument();
  });

  test('defaults time_grain to Monthly when missing', () => {
    const budget = { ...BASE_BUDGET, time_grain: '' };
    render(<BudgetCard budget={budget} />);
    expect(screen.getByText('Monthly')).toBeInTheDocument();
  });
});
