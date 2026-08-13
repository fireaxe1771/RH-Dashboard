import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { AiAnalyticsFilterBar } from '../components/ai/AiAnalyticsFilterBar';

describe('AiAnalyticsFilterBar', () => {
  const defaultProps = {
    startDate: '2026-01-01',
    endDate: '2026-01-31',
    onStartDateChange: vi.fn(),
    onEndDateChange: vi.fn(),
  };

  test('renders date inputs and quick range buttons', () => {
    render(<AiAnalyticsFilterBar {...defaultProps} />);
    expect(screen.getByText('Start Date')).toBeInTheDocument();
    expect(screen.getByText('End Date')).toBeInTheDocument();
    expect(screen.getByText('7D')).toBeInTheDocument();
    expect(screen.getByText('30D')).toBeInTheDocument();
    expect(screen.getByText('90D')).toBeInTheDocument();
    expect(screen.getByText('1Y')).toBeInTheDocument();
  });

  test('calls onStartDateChange when start date input changes', () => {
    const onStartDateChange = vi.fn();
    render(<AiAnalyticsFilterBar {...defaultProps} onStartDateChange={onStartDateChange} />);
    const inputs = screen.getAllByDisplayValue('2026-01-01');
    fireEvent.change(inputs[0], { target: { value: '2026-02-01' } });
    expect(onStartDateChange).toHaveBeenCalledWith('2026-02-01');
  });

  test('calls onEndDateChange when end date input changes', () => {
    const onEndDateChange = vi.fn();
    render(<AiAnalyticsFilterBar {...defaultProps} onEndDateChange={onEndDateChange} />);
    const inputs = screen.getAllByDisplayValue('2026-01-31');
    fireEvent.change(inputs[0], { target: { value: '2026-02-28' } });
    expect(onEndDateChange).toHaveBeenCalledWith('2026-02-28');
  });

  test('quick range buttons set both start and end dates', () => {
    const onStartDateChange = vi.fn();
    const onEndDateChange = vi.fn();
    render(
      <AiAnalyticsFilterBar
        {...defaultProps}
        onStartDateChange={onStartDateChange}
        onEndDateChange={onEndDateChange}
      />
    );
    fireEvent.click(screen.getByText('30D'));
    expect(onStartDateChange).toHaveBeenCalledTimes(1);
    expect(onEndDateChange).toHaveBeenCalledTimes(1);
    // The start date should be 30 days before today (YYYY-MM-DD format)
    const startArg = onStartDateChange.mock.calls[0][0];
    const endArg = onEndDateChange.mock.calls[0][0];
    expect(startArg).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(endArg).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  test('renders department filter when onDepartmentIdChange is provided', () => {
    const onDepartmentIdChange = vi.fn();
    render(<AiAnalyticsFilterBar {...defaultProps} onDepartmentIdChange={onDepartmentIdChange} />);
    expect(screen.getByText('Department ID')).toBeInTheDocument();
  });

  test('does not render department filter when onDepartmentIdChange is omitted', () => {
    render(<AiAnalyticsFilterBar {...defaultProps} />);
    expect(screen.queryByText('Department ID')).not.toBeInTheDocument();
  });

  test('department filter calls handler with number value', () => {
    const onDepartmentIdChange = vi.fn();
    render(<AiAnalyticsFilterBar {...defaultProps} onDepartmentIdChange={onDepartmentIdChange} />);
    const deptInput = screen.getByPlaceholderText('All');
    fireEvent.change(deptInput, { target: { value: '42' } });
    expect(onDepartmentIdChange).toHaveBeenCalledWith(42);
  });

  test('department filter calls handler with undefined for empty value', () => {
    const onDepartmentIdChange = vi.fn();
    render(
      <AiAnalyticsFilterBar
        {...defaultProps}
        departmentId={42}
        onDepartmentIdChange={onDepartmentIdChange}
      />
    );
    // Use placeholderText instead of displayValue — number inputs in jsdom
    // don't reliably expose their value via getByDisplayValue.
    const deptInput = screen.getByPlaceholderText('All');
    // Simulate clearing the number input
    fireEvent.change(deptInput, { target: { value: '' } });
    expect(onDepartmentIdChange).toHaveBeenCalled();
    const lastCall = onDepartmentIdChange.mock.calls[onDepartmentIdChange.mock.calls.length - 1];
    expect(lastCall[0]).toBeUndefined();
  });

  test('renders business outcome filter by default', () => {
    const onBusinessOutcomeChange = vi.fn();
    render(<AiAnalyticsFilterBar {...defaultProps} onBusinessOutcomeChange={onBusinessOutcomeChange} />);
    expect(screen.getByText('Business Outcome')).toBeInTheDocument();
    // Options: All, Released, Cancelled / Rejected, Pending, Unknown
    expect(screen.getByText('Released')).toBeInTheDocument();
    expect(screen.getByText('Pending')).toBeInTheDocument();
  });

  test('hides business outcome filter when showOutcomeFilter is false', () => {
    const onBusinessOutcomeChange = vi.fn();
    render(
      <AiAnalyticsFilterBar
        {...defaultProps}
        onBusinessOutcomeChange={onBusinessOutcomeChange}
        showOutcomeFilter={false}
      />
    );
    expect(screen.queryByText('Business Outcome')).not.toBeInTheDocument();
  });

  test('business outcome select calls handler with string or undefined', () => {
    const onBusinessOutcomeChange = vi.fn();
    render(<AiAnalyticsFilterBar {...defaultProps} onBusinessOutcomeChange={onBusinessOutcomeChange} />);
    const select = screen.getByDisplayValue('All');
    fireEvent.change(select, { target: { value: 'released' } });
    expect(onBusinessOutcomeChange).toHaveBeenCalledWith('released');
    fireEvent.change(select, { target: { value: '' } });
    expect(onBusinessOutcomeChange).toHaveBeenLastCalledWith(undefined);
  });
});
