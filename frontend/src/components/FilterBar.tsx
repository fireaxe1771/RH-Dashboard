import React, { useEffect, useState } from 'react';
import { api, FilterOptions } from '../services/api';
import { Calendar, Filter, Users, ShieldAlert, AlertCircle, Clock } from 'lucide-react';

export type RangeType = 'day' | 'week' | 'month' | 'year';

/** The default period every dashboard opens on. Defined once so a filter bar
 *  and the hook that seeds it can never disagree about the initial range. */
export const DEFAULT_RANGE_TYPE: RangeType = 'week';

export interface DashboardFilters {
  department_id?: string;
  processor_id?: string;
  start_date?: string;
  end_date?: string;
  range_type?: RangeType;
  periods_back?: number;
}

interface FilterBarProps {
  filters: DashboardFilters;
  onChange: (filters: DashboardFilters) => void;
  /** ISO date string from the database server (GETDATE()). When set, all
   *  date-range calculations use this instead of the browser clock. */
  serverDate?: string;
}

// ── Date ranges ─────────────────────────────────────────────────────────
// Period arithmetic (what "current week" / "last month" resolves to) lives
// ONLY in the backend: target_db.compute_date_range, exposed as
// GET /api/date-range and reached via api.getDateRange().
//
// It deliberately does not exist here. A second TypeScript implementation
// existed previously and drifted from the Python one — the same off-by-a-period
// bug had to be fixed twice, in two languages. Call api.getDateRange() instead
// of recomputing dates locally.

// Build dropdown options for each range type
export function periodOptions(rangeType: RangeType): { value: number; label: string }[] {
  if (rangeType === 'week') {
    return [
      { value: 0, label: 'Current Week' },
      ...Array.from({ length: 12 }, (_, i) => ({
        value: i + 1,
        label: `${i + 1} ${i === 0 ? 'Week' : 'Weeks'} Ago`,
      })),
    ];
  }
  if (rangeType === 'month') {
    return [
      { value: 0, label: 'Current Month' },
      ...Array.from({ length: 12 }, (_, i) => ({
        value: i + 1,
        label: `${i + 1} ${i === 0 ? 'Month' : 'Months'} Ago`,
      })),
    ];
  }
  if (rangeType === 'year') {
    return [
      { value: 0, label: 'Current Year' },
      ...Array.from({ length: 5 }, (_, i) => ({
        value: i + 1,
        label: `${i + 1} ${i === 0 ? 'Year' : 'Years'} Ago`,
      })),
    ];
  }
  return [];
}

export const FilterBar: React.FC<FilterBarProps> = ({ filters, onChange, serverDate }) => {
  const [options, setOptions] = useState<FilterOptions>({
    departments: [],
    processors: [],
    claimTypes: []
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api.getFilterOptions()
      .then((data) => {
        if (active) {
          setOptions(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        console.error("Failed to load filter options:", err);
        if (active) {
          setError(err.message || "Failed to load database filter options from server.");
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, []);

  const rangeType = filters.range_type || 'week';
  const periodsBack = filters.periods_back ?? 0;

  const handleRangeTypeChange = async (newType: RangeType) => {
    const pb = 0;
    if (newType === 'day') {
      // Keep current dates, just switch mode
      onChange({ ...filters, range_type: newType, periods_back: pb });
      return;
    }
    // range_type/periods_back are sent to the backend, which recomputes the
    // range server-side anyway; the resolved dates here are for display. If
    // the lookup fails, still switch mode so the UI stays responsive.
    try {
      const dates = await api.getDateRange(newType, pb);
      onChange({
        ...filters,
        range_type: newType,
        periods_back: pb,
        start_date: dates.start_date,
        end_date: dates.end_date,
      });
    } catch (err) {
      console.error('Failed to resolve date range:', err);
      onChange({ ...filters, range_type: newType, periods_back: pb });
    }
  };

  const handlePeriodsBackChange = async (pb: number) => {
    try {
      const dates = await api.getDateRange(rangeType, pb);
      onChange({
        ...filters,
        periods_back: pb,
        start_date: dates.start_date,
        end_date: dates.end_date,
      });
    } catch (err) {
      console.error('Failed to resolve date range:', err);
      onChange({ ...filters, periods_back: pb });
    }
  };

  const handleSelectChange = (key: keyof DashboardFilters, value: string) => {
    onChange({ ...filters, [key]: value || undefined });
  };

  const handleDateChange = (key: 'start_date' | 'end_date', value: string) => {
    onChange({ ...filters, [key]: value || undefined });
  };

  if (error) {
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
          padding: '16px 24px',
          backgroundColor: 'rgba(239, 68, 68, 0.1)',
          borderRadius: 'var(--border-radius-md)',
          border: '1px solid rgba(239, 68, 68, 0.2)',
          color: 'var(--color-danger)',
          fontSize: '14px',
          fontWeight: 500
        }}
      >
        <AlertCircle size={20} />
        <span><strong>Configuration/Connection Error:</strong> {error}</span>
      </div>
    );
  }

  const periods = periodOptions(rangeType);

  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: '16px',
        alignItems: 'center',
        padding: '16px 24px',
        backgroundColor: 'var(--bg-secondary)',
        borderRadius: 'var(--border-radius-md)',
        border: '1px solid var(--border-color)',
        boxShadow: 'var(--shadow-sm)'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-secondary)', fontSize: '14px', fontWeight: 500 }}>
        <Filter size={16} style={{ color: 'var(--accent-primary)' }} />
        <span>Filters:</span>
      </div>

      {/* Range Type Selector */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', minWidth: '130px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
          <Clock size={10} />
          <span>Range</span>
        </div>
        <select
          className="input"
          value={rangeType}
          onChange={(e) => handleRangeTypeChange(e.target.value as RangeType)}
          style={{ padding: '8px 12px', fontSize: '13px' }}
          disabled={loading}
        >
          <option value="day">Day Range</option>
          <option value="week">Week</option>
          <option value="month">Month</option>
          <option value="year">Year</option>
        </select>
      </div>

      {/* Period Selector (for week/month/year) or Date Pickers (for day) */}
      {rangeType === 'day' ? (
        <div style={{ display: 'flex', gap: '12px' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', width: '150px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
              <Calendar size={10} />
              <span>Start Date</span>
            </div>
            <input
              type="date"
              className="input"
              value={filters.start_date || ''}
              onChange={(e) => handleDateChange('start_date', e.target.value)}
              style={{ padding: '7px 12px', fontSize: '13px' }}
              disabled={loading}
            />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', width: '150px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
              <Calendar size={10} />
              <span>End Date</span>
            </div>
            <input
              type="date"
              className="input"
              value={filters.end_date || ''}
              onChange={(e) => handleDateChange('end_date', e.target.value)}
              style={{ padding: '7px 12px', fontSize: '13px' }}
              disabled={loading}
            />
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', minWidth: '180px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
            <Calendar size={10} />
            <span>Period</span>
          </div>
          <select
            className="input"
            value={periodsBack}
            onChange={(e) => handlePeriodsBackChange(Number(e.target.value))}
            style={{ padding: '8px 12px', fontSize: '13px' }}
            disabled={loading}
          >
            {periods.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>
      )}

      {/* Date range display (read-only summary for week/month/year) */}
      {rangeType !== 'day' && filters.start_date && filters.end_date && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          fontSize: '12px',
          color: 'var(--text-muted)',
          padding: '6px 12px',
          backgroundColor: 'rgba(99, 102, 241, 0.08)',
          borderRadius: 'var(--border-radius-md)',
          border: '1px solid rgba(99, 102, 241, 0.15)',
        }}>
          <Calendar size={12} style={{ color: 'var(--accent-primary)' }} />
          <span>{filters.start_date} &mdash; {filters.end_date}</span>
        </div>
      )}

      {/* Department Dropdown */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', minWidth: '200px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
          <ShieldAlert size={10} />
          <span>Fire Department</span>
        </div>
        <select
          className="input"
          value={filters.department_id || ''}
          onChange={(e) => handleSelectChange('department_id', e.target.value)}
          style={{ padding: '8px 12px', fontSize: '13px' }}
          disabled={loading}
        >
          <option value="">All Departments</option>
          {options.departments.map((dept) => (
            <option key={dept.id} value={dept.id}>{dept.name}</option>
          ))}
        </select>
      </div>

      {/* Claims Processor Dropdown */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', minWidth: '200px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
          <Users size={10} />
          <span>Claims Processor</span>
        </div>
        <select
          className="input"
          value={filters.processor_id || ''}
          onChange={(e) => handleSelectChange('processor_id', e.target.value)}
          style={{ padding: '8px 12px', fontSize: '13px' }}
          disabled={loading}
        >
          <option value="">All Processors</option>
          {options.processors.map((proc) => (
            <option key={proc.id} value={proc.id}>{proc.name}</option>
          ))}
        </select>
      </div>
    </div>
  );
};
