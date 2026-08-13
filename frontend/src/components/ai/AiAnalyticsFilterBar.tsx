import React, { useMemo, useState } from 'react';
import { Calendar, Clock } from 'lucide-react';
import { billingStyles } from '../billing/shared';
import { AiAnalyticsFilters } from '../../services/aiAnalyticsApi';
import {
  RangeType,
  computeDateRange,
  periodOptions,
} from '../FilterBar';

interface Props {
  startDate: string;
  endDate: string;
  onStartDateChange: (date: string) => void;
  onEndDateChange: (date: string) => void;
  /** ISO date string from the database server (GETDATE()). When set, all
   *  date-range calculations use this instead of the browser clock. */
  serverDate?: string;
  /** Initial range type for the selector. Defaults to 'month'. */
  defaultRangeType?: RangeType;
  /** Initial periods-back value. Defaults to 0 (current period). */
  defaultPeriodsBack?: number;
  departmentId?: number;
  onDepartmentIdChange?: (id: number | undefined) => void;
  businessOutcome?: string;
  onBusinessOutcomeChange?: (outcome: string | undefined) => void;
  showOutcomeFilter?: boolean;
}

const inputStyle: React.CSSProperties = {
  backgroundColor: 'var(--bg-primary)',
  border: '1px solid var(--border-color)',
  borderRadius: 'var(--border-radius-md)',
  padding: '8px 12px',
  fontSize: '13px',
  color: 'var(--text-primary)',
  outline: 'none',
};

const labelStyle: React.CSSProperties = {
  fontSize: '11px',
  fontWeight: 600,
  color: 'var(--text-muted)',
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
};

export const AiAnalyticsFilterBar: React.FC<Props> = ({
  startDate,
  endDate,
  onStartDateChange,
  onEndDateChange,
  serverDate,
  defaultRangeType = 'month',
  defaultPeriodsBack = 0,
  departmentId,
  onDepartmentIdChange,
  businessOutcome,
  onBusinessOutcomeChange,
  showOutcomeFilter = true,
}) => {
  const [rangeType, setRangeType] = useState<RangeType>(defaultRangeType);
  const [periodsBack, setPeriodsBack] = useState<number>(defaultPeriodsBack);

  const periods = useMemo(() => periodOptions(rangeType), [rangeType]);

  const handleRangeTypeChange = (newType: RangeType) => {
    setRangeType(newType);
    setPeriodsBack(0);
    if (newType !== 'day') {
      const dates = computeDateRange(newType, 0, serverDate);
      onStartDateChange(dates.start_date);
      onEndDateChange(dates.end_date);
    }
    // For 'day' (Custom), keep current dates — user will pick manually
  };

  const handlePeriodsBackChange = (pb: number) => {
    setPeriodsBack(pb);
    const dates = computeDateRange(rangeType, pb, serverDate);
    onStartDateChange(dates.start_date);
    onEndDateChange(dates.end_date);
  };

  return (
    <div style={billingStyles.card}>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: '16px',
          alignItems: 'flex-end',
        }}
      >
        {/* Range Type Selector */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', minWidth: '130px' }}>
          <span style={labelStyle}>
            <Clock size={10} style={{ verticalAlign: 'text-bottom', marginRight: '4px' }} />
            Range
          </span>
          <select
            value={rangeType}
            onChange={(e) => handleRangeTypeChange(e.target.value as RangeType)}
            style={inputStyle}
          >
            <option value="day">Custom</option>
            <option value="week">Week</option>
            <option value="month">Month</option>
            <option value="year">Year</option>
          </select>
        </div>

        {/* Period Selector (for week/month/year) or Date Pickers (for custom) */}
        {rangeType === 'day' ? (
          <div style={{ display: 'flex', gap: '12px' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <span style={labelStyle}>
                <Calendar size={10} style={{ verticalAlign: 'text-bottom', marginRight: '4px' }} />
                Start Date
              </span>
              <input
                type="date"
                value={startDate}
                onChange={(e) => onStartDateChange(e.target.value)}
                style={inputStyle}
              />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <span style={labelStyle}>
                <Calendar size={10} style={{ verticalAlign: 'text-bottom', marginRight: '4px' }} />
                End Date
              </span>
              <input
                type="date"
                value={endDate}
                onChange={(e) => onEndDateChange(e.target.value)}
                style={inputStyle}
              />
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', minWidth: '180px' }}>
            <span style={labelStyle}>
              <Calendar size={10} style={{ verticalAlign: 'text-bottom', marginRight: '4px' }} />
              Period
            </span>
            <select
              value={periodsBack}
              onChange={(e) => handlePeriodsBackChange(Number(e.target.value))}
              style={inputStyle}
            >
              {periods.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>
        )}

        {/* Date range display (read-only summary for week/month/year) */}
        {rangeType !== 'day' && startDate && endDate && (
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
            <span>{startDate} &mdash; {endDate}</span>
          </div>
        )}

        {onDepartmentIdChange && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <span style={labelStyle}>Department ID</span>
            <input
              type="number"
              value={departmentId ?? ''}
              placeholder="All"
              onChange={(e) =>
                onDepartmentIdChange(e.target.value ? Number(e.target.value) : undefined)
              }
              style={{ ...inputStyle, width: '100px' }}
            />
          </div>
        )}

        {showOutcomeFilter && onBusinessOutcomeChange && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <span style={labelStyle}>Business Outcome</span>
            <select
              value={businessOutcome ?? ''}
              onChange={(e) => onBusinessOutcomeChange(e.target.value || undefined)}
              style={inputStyle}
            >
              <option value="">All</option>
              <option value="released">Released</option>
              <option value="cancelled_rejected">Cancelled / Rejected</option>
              <option value="pending">Pending</option>
              <option value="unknown">Unknown</option>
            </select>
          </div>
        )}
      </div>
    </div>
  );
};
