import React, { useMemo } from 'react';
import { Calendar } from 'lucide-react';
import { billingStyles } from '../billing/shared';
import { AiAnalyticsFilters } from '../../services/aiAnalyticsApi';

const fmt = (d: Date): string => d.toISOString().split('T')[0];

interface Props {
  startDate: string;
  endDate: string;
  onStartDateChange: (date: string) => void;
  onEndDateChange: (date: string) => void;
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
  departmentId,
  onDepartmentIdChange,
  businessOutcome,
  onBusinessOutcomeChange,
  showOutcomeFilter = true,
}) => {
  const quickRanges = useMemo(
    () => [
      { label: '7D', days: 7 },
      { label: '30D', days: 30 },
      { label: '90D', days: 90 },
      { label: '1Y', days: 365 },
    ],
    [],
  );

  const applyQuickRange = (days: number) => {
    const end = new Date();
    const start = new Date();
    start.setDate(start.getDate() - days);
    onStartDateChange(fmt(start));
    onEndDateChange(fmt(end));
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
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <span style={labelStyle}>Start Date</span>
          <input
            type="date"
            value={startDate}
            onChange={(e) => onStartDateChange(e.target.value)}
            style={inputStyle}
          />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <span style={labelStyle}>End Date</span>
          <input
            type="date"
            value={endDate}
            onChange={(e) => onEndDateChange(e.target.value)}
            style={inputStyle}
          />
        </div>

        <div style={{ display: 'flex', gap: '4px' }}>
          {quickRanges.map((r) => (
            <button
              key={r.label}
              onClick={() => applyQuickRange(r.days)}
              style={{
                padding: '6px 12px',
                fontSize: '12px',
                fontWeight: 600,
                border: '1px solid var(--border-color)',
                borderRadius: 'var(--border-radius-md)',
                backgroundColor: 'var(--bg-primary)',
                color: 'var(--text-secondary)',
                cursor: 'pointer',
              }}
            >
              {r.label}
            </button>
          ))}
        </div>

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
