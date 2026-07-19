import { describe, test, expect } from 'vitest';
import {
  formatCurrency,
  formatPercent,
  utilizationColor,
  currentPeriod,
  LoadingState,
  ErrorState,
  EmptyState,
} from '../components/billing/shared';
import { render, screen } from '@testing-library/react';
import React from 'react';

describe('shared utilities', () => {
  describe('formatCurrency', () => {
    test('formats a positive number as USD currency', () => {
      expect(formatCurrency(1234.5)).toBe('$1,234.50');
    });

    test('respects custom currency code', () => {
      expect(formatCurrency(100, 'EUR')).toMatch(/100/);
    });

    test('treats null/undefined/NaN as 0', () => {
      expect(formatCurrency(null)).toBe('$0.00');
      expect(formatCurrency(undefined)).toBe('$0.00');
      expect(formatCurrency(NaN)).toBe('$0.00');
      expect(formatCurrency(Infinity)).toBe('$0.00');
    });

    test('falls back to $ prefix on invalid currency code', () => {
      expect(formatCurrency(50, 'NOTACURRENCY')).toBe('$50.00');
    });
  });

  describe('formatPercent', () => {
    test('formats with default 1 decimal', () => {
      expect(formatPercent(42.56)).toBe('42.6%');
    });

    test('respects custom digit count', () => {
      expect(formatPercent(42.5678, 3)).toBe('42.568%');
    });

    test('treats non-finite as 0', () => {
      expect(formatPercent(null)).toBe('0.0%');
      expect(formatPercent(undefined)).toBe('0.0%');
      expect(formatPercent(NaN)).toBe('0.0%');
    });
  });

  describe('utilizationColor', () => {
    test('returns danger red for over 100%', () => {
      expect(utilizationColor(105)).toBe('#b91c1c');
    });

    test('returns danger CSS var for 91-100%', () => {
      expect(utilizationColor(95)).toBe('var(--color-danger)');
    });

    test('returns warning CSS var for 71-90%', () => {
      expect(utilizationColor(75)).toBe('var(--color-warning)');
    });

    test('returns success CSS var for <= 70%', () => {
      expect(utilizationColor(50)).toBe('var(--color-success)');
    });

    test('boundary: exactly 100 uses danger var, not red', () => {
      expect(utilizationColor(100)).toBe('var(--color-danger)');
    });

    test('boundary: exactly 90 uses warning', () => {
      expect(utilizationColor(90)).toBe('var(--color-warning)');
    });

    test('boundary: exactly 70 uses success', () => {
      expect(utilizationColor(70)).toBe('var(--color-success)');
    });
  });

  describe('currentPeriod', () => {
    test('returns YYYY-MM format string', () => {
      const period = currentPeriod();
      expect(period).toMatch(/^\d{4}-\d{2}$/);
    });
  });

  describe('LoadingState', () => {
    test('renders default label', () => {
      render(<LoadingState />);
      expect(screen.getByText('Loading…')).toBeInTheDocument();
    });

    test('renders custom label', () => {
      render(<LoadingState label="Fetching data…" />);
      expect(screen.getByText('Fetching data…')).toBeInTheDocument();
    });
  });

  describe('ErrorState', () => {
    test('renders the error message', () => {
      render(<ErrorState message="Something broke" />);
      expect(screen.getByText(/Something broke/)).toBeInTheDocument();
    });
  });

  describe('EmptyState', () => {
    test('renders default label', () => {
      render(<EmptyState />);
      expect(screen.getByText('No data available.')).toBeInTheDocument();
    });

    test('renders custom label', () => {
      render(<EmptyState label="No budgets found." />);
      expect(screen.getByText('No budgets found.')).toBeInTheDocument();
    });
  });
});
