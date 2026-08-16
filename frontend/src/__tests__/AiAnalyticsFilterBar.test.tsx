import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import { AiAnalyticsFilterBar } from '../components/ai/AiAnalyticsFilterBar';

// The filter bar resolves period boundaries via the backend (the single source
// of range arithmetic), so the API call is mocked rather than recomputed here.
vi.mock('../services/api', () => ({
  api: {
    getDateRange: vi.fn().mockResolvedValue({
      server_date: '2026-08-13',
      start_date: '2026-08-09',
      end_date: '2026-08-15',
    }),
  },
}));

describe('AiAnalyticsFilterBar', () => {
  const defaultProps = {
    startDate: '2026-01-01',
    endDate: '2026-01-31',
    onStartDateChange: vi.fn(),
    onEndDateChange: vi.fn(),
  };

  test('renders range type selector with Custom/Week/Month/Year options', () => {
    render(<AiAnalyticsFilterBar {...defaultProps} />);
    const select = screen.getByDisplayValue('Week');
    expect(select).toBeInTheDocument();
    // Verify all options exist
    const options = Array.from(select.querySelectorAll('option')).map((o) => o.textContent);
    expect(options).toContain('Custom');
    expect(options).toContain('Week');
    expect(options).toContain('Month');
    expect(options).toContain('Year');
  });

  test('shows period selector (not date inputs) in a period mode', () => {
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

  test('switching range type applies the range resolved by the backend', async () => {
    const onStartDateChange = vi.fn();
    const onEndDateChange = vi.fn();
    render(
      <AiAnalyticsFilterBar
        {...defaultProps}
        onStartDateChange={onStartDateChange}
        onEndDateChange={onEndDateChange}
      />
    );
    const select = screen.getByDisplayValue('Week');
    fireEvent.change(select, { target: { value: 'month' } });

    // Dates come from GET /api/date-range, not from local arithmetic.
    await waitFor(() => expect(onStartDateChange).toHaveBeenCalledTimes(1));
    expect(onEndDateChange).toHaveBeenCalledTimes(1);
    expect(onStartDateChange).toHaveBeenCalledWith('2026-08-09');
    expect(onEndDateChange).toHaveBeenCalledWith('2026-08-15');
  });

  test('changing period applies the range resolved by the backend', async () => {
    const onStartDateChange = vi.fn();
    const onEndDateChange = vi.fn();
    render(
      <AiAnalyticsFilterBar
        {...defaultProps}
        onStartDateChange={onStartDateChange}
        onEndDateChange={onEndDateChange}
      />
    );
    // Default is week mode - find the period select (Current Week)
    const periodSelect = screen.getByDisplayValue('Current Week');
    fireEvent.change(periodSelect, { target: { value: '1' } });

    await waitFor(() => expect(onStartDateChange).toHaveBeenCalledTimes(1));
    expect(onEndDateChange).toHaveBeenCalledTimes(1);
  });

  test('switching to Custom does not overwrite the dates', async () => {
    const onStartDateChange = vi.fn();
    const onEndDateChange = vi.fn();
    render(
      <AiAnalyticsFilterBar
        {...defaultProps}
        onStartDateChange={onStartDateChange}
        onEndDateChange={onEndDateChange}
      />
    );
    fireEvent.change(screen.getByDisplayValue('Week'), { target: { value: 'day' } });
    await waitFor(() => expect(screen.getByText('Start Date')).toBeInTheDocument());
    expect(onStartDateChange).not.toHaveBeenCalled();
    expect(onEndDateChange).not.toHaveBeenCalled();
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
