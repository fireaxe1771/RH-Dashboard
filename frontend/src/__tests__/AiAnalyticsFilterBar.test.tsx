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

  test('renders range type selector with Custom/Week/Month/Year options', () => {
    render(<AiAnalyticsFilterBar {...defaultProps} />);
    const select = screen.getByDisplayValue('Month');
    expect(select).toBeInTheDocument();
    // Verify all options exist
    const options = Array.from(select.querySelectorAll('option')).map((o) => o.textContent);
    expect(options).toContain('Custom');
    expect(options).toContain('Week');
    expect(options).toContain('Month');
    expect(options).toContain('Year');
  });

  test('shows period selector (not date inputs) in month mode', () => {
    render(<AiAnalyticsFilterBar {...defaultProps} />);
    expect(screen.getByText('Period')).toBeInTheDocument();
    expect(screen.queryByText('Start Date')).not.toBeInTheDocument();
    expect(screen.queryByText('End Date')).not.toBeInTheDocument();
  });

  test('shows date inputs when range type is Custom', () => {
    render(<AiAnalyticsFilterBar {...defaultProps} defaultRangeType="day" />);
    expect(screen.getByText('Start Date')).toBeInTheDocument();
    expect(screen.getByText('End Date')).toBeInTheDocument();
  });

  test('calls onStartDateChange when custom start date input changes', () => {
    const onStartDateChange = vi.fn();
    render(
      <AiAnalyticsFilterBar
        {...defaultProps}
        defaultRangeType="day"
        onStartDateChange={onStartDateChange}
      />
    );
    const inputs = screen.getAllByDisplayValue('2026-01-01');
    fireEvent.change(inputs[0], { target: { value: '2026-02-01' } });
    expect(onStartDateChange).toHaveBeenCalledWith('2026-02-01');
  });

  test('calls onEndDateChange when custom end date input changes', () => {
    const onEndDateChange = vi.fn();
    render(
      <AiAnalyticsFilterBar
        {...defaultProps}
        defaultRangeType="day"
        onEndDateChange={onEndDateChange}
      />
    );
    const inputs = screen.getAllByDisplayValue('2026-01-31');
    fireEvent.change(inputs[0], { target: { value: '2026-02-28' } });
    expect(onEndDateChange).toHaveBeenCalledWith('2026-02-28');
  });

  test('switching to week range type calls date change handlers with computed range', () => {
    const onStartDateChange = vi.fn();
    const onEndDateChange = vi.fn();
    render(
      <AiAnalyticsFilterBar
        {...defaultProps}
        onStartDateChange={onStartDateChange}
        onEndDateChange={onEndDateChange}
        serverDate="2026-08-13"
      />
    );
    const select = screen.getByDisplayValue('Month');
    fireEvent.change(select, { target: { value: 'week' } });
    expect(onStartDateChange).toHaveBeenCalledTimes(1);
    expect(onEndDateChange).toHaveBeenCalledTimes(1);
    // Computed dates should be YYYY-MM-DD
    expect(onStartDateChange.mock.calls[0][0]).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(onEndDateChange.mock.calls[0][0]).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  test('changing period calls date change handlers with computed range', () => {
    const onStartDateChange = vi.fn();
    const onEndDateChange = vi.fn();
    render(
      <AiAnalyticsFilterBar
        {...defaultProps}
        onStartDateChange={onStartDateChange}
        onEndDateChange={onEndDateChange}
        serverDate="2026-08-13"
      />
    );
    // Default is month mode — find the period select (Current Month)
    const periodSelect = screen.getByDisplayValue('Current Month');
    fireEvent.change(periodSelect, { target: { value: '1' } });
    expect(onStartDateChange).toHaveBeenCalledTimes(1);
    expect(onEndDateChange).toHaveBeenCalledTimes(1);
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
    const deptInput = screen.getByPlaceholderText('All');
    fireEvent.change(deptInput, { target: { value: '' } });
    expect(onDepartmentIdChange).toHaveBeenCalled();
    const lastCall = onDepartmentIdChange.mock.calls[onDepartmentIdChange.mock.calls.length - 1];
    expect(lastCall[0]).toBeUndefined();
  });

  test('renders business outcome filter by default', () => {
    const onBusinessOutcomeChange = vi.fn();
    render(<AiAnalyticsFilterBar {...defaultProps} onBusinessOutcomeChange={onBusinessOutcomeChange} />);
    expect(screen.getByText('Business Outcome')).toBeInTheDocument();
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
