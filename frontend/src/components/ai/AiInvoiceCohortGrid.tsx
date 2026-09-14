import React, { useMemo, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Download } from 'lucide-react';
import { billingStyles, LoadingState, ErrorState, EmptyState, formatCurrency } from '../billing/shared';
import { exportToCsv } from '../../utils/export';
import {
  aiAnalyticsApi,
  AiAnalyticsFilters,
  AiInvoiceListItem,
  AiInvoiceCohortResponse,
  AiResult,
  AI_RESULT_LABELS,
  AI_STUCK_FALLBACK_REASON,
  AI_EXECUTION_FAILED_FALLBACK_REASON,
  aiResultBadgeStyle,
  formatProcessingAge,
} from '../../services/aiAnalyticsApi';

interface Props {
  filters: AiAnalyticsFilters;
  onRowClick?: (claimId: number) => void;
  onClearResultFilter?: () => void;
}

const outcomeBadgeStyle = (outcome: string): React.CSSProperties => {
  switch (outcome) {
    case 'released':
      return { backgroundColor: 'rgba(34, 197, 94, 0.15)', color: '#22c55e', padding: '2px 8px', borderRadius: '4px', fontSize: '11px', fontWeight: 600 };
    case 'cancelled_rejected':
      return { backgroundColor: 'rgba(239, 68, 68, 0.15)', color: '#ef4444', padding: '2px 8px', borderRadius: '4px', fontSize: '11px', fontWeight: 600 };
    case 'pending':
      return { backgroundColor: 'rgba(234, 179, 8, 0.15)', color: '#eab308', padding: '2px 8px', borderRadius: '4px', fontSize: '11px', fontWeight: 600 };
    default:
      return { backgroundColor: 'rgba(148, 163, 184, 0.15)', color: '#94a3b8', padding: '2px 8px', borderRadius: '4px', fontSize: '11px', fontWeight: 600 };
  }
};

const REASON_PREVIEW_LEN = 80;

const ReasonCell: React.FC<{ inv: AiInvoiceListItem }> = ({ inv }) => {
  const [expanded, setExpanded] = useState(false);
  const message = inv.review_message;

  if (inv.ai_result === 'stuck' && !message) {
    return (
      <span style={{ fontSize: '12px', color: 'var(--text-muted)', whiteSpace: 'normal' }}>
        {AI_STUCK_FALLBACK_REASON}
      </span>
    );
  }
  if (inv.ai_result === 'execution_failed' && !message) {
    return (
      <span style={{ fontSize: '12px', color: 'var(--text-muted)', whiteSpace: 'normal' }}>
        {AI_EXECUTION_FAILED_FALLBACK_REASON}
      </span>
    );
  }
  if (!message) return <span>—</span>;

  const isLong = message.length > REASON_PREVIEW_LEN;
  const shown = expanded || !isLong ? message : `${message.slice(0, REASON_PREVIEW_LEN)}…`;
  return (
    <span style={{ fontSize: '12px', whiteSpace: 'normal' }}>
      {shown}
      {isLong && (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={(e) => {
            e.stopPropagation();
            setExpanded((v) => !v);
          }}
          style={{
            marginLeft: '6px',
            border: 'none',
            background: 'none',
            padding: 0,
            fontSize: '11px',
            fontWeight: 600,
            color: 'var(--accent-primary)',
            cursor: 'pointer',
          }}
        >
          {expanded ? 'Hide reason' : 'Show reason'}
        </button>
      )}
    </span>
  );
};

const tableHeaderStyle: React.CSSProperties = {
  fontSize: '11px',
  fontWeight: 600,
  color: 'var(--text-muted)',
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
  padding: '8px 12px',
  textAlign: 'left',
  borderBottom: '1px solid var(--border-color)',
  whiteSpace: 'nowrap',
};

const tableCellStyle: React.CSSProperties = {
  padding: '8px 12px',
  fontSize: '13px',
  color: 'var(--text-primary)',
  borderBottom: '1px solid var(--border-color)',
  whiteSpace: 'nowrap',
};

export const AiInvoiceCohortGrid: React.FC<Props> = ({ filters, onRowClick, onClearResultFilter }) => {
  const [data, setData] = useState<AiInvoiceCohortResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const pageSize = 50;

  // Any filter change (including ai_result drill-downs) must restart at
  // page 1 — otherwise a stale page >1 can render an empty grid.
  const filterKey = useMemo(() => JSON.stringify(filters), [filters]);
  const prevFilterKeyRef = useRef(filterKey);

  React.useEffect(() => {
    let active = true;
    const requestPage = prevFilterKeyRef.current !== filterKey ? 1 : page;
    prevFilterKeyRef.current = filterKey;
    if (requestPage !== page) setPage(1);
    setLoading(true);
    setError(null);
    const fullFilters = { ...filters, page: requestPage, page_size: pageSize };
    aiAnalyticsApi
      .getInvoiceCohort(fullFilters)
      .then((res) => {
        if (active) {
          setData(res);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (active) {
          setError(err.message || 'Failed to load invoice cohort.');
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [filters, filterKey, page]);

  const columns = useMemo(
    () => [
      'Claim ID',
      'Department',
      'Run #',
      'Business Outcome',
      'Final AI Result',
      'Reason',
      'AI Items',
      'Confidence',
      'Invoice Total',
      'AI Last Updated',
      'Processing Age',
    ],
    [],
  );

  const handleExport = () => {
    if (!data) return;
    const csvColumns = [
      'Claim ID',
      'Department',
      'Run #',
      'Business Outcome',
      'Final AI Result',
      'AI Review Reason',
      'AI Items',
      'Confidence',
      'Invoice Total',
      'AI Last Updated',
      'Processing Age Seconds',
    ];
    const rows = data.invoices.map((inv) => ({
      'Claim ID': inv.claim_id,
      Department: inv.department_name || '—',
      'Run #': inv.run_number || '—',
      'Business Outcome': inv.business_outcome,
      'Final AI Result': AI_RESULT_LABELS[inv.ai_result] ?? inv.ai_result,
      'AI Review Reason': inv.review_message
        ?? (inv.ai_result === 'stuck'
          ? AI_STUCK_FALLBACK_REASON
          : inv.ai_result === 'execution_failed'
            ? AI_EXECUTION_FAILED_FALLBACK_REASON
            : inv.raw_rejection_reason ?? '—'),
      'AI Items': inv.ai_line_item_count,
      Confidence: inv.confidence ?? '—',
      'Invoice Total': inv.invoice_total ?? 0,
      'AI Last Updated': inv.ai_updated_at || '—',
      'Processing Age Seconds': inv.processing_age_seconds ?? '—',
    }));
    exportToCsv('AI_Invoice_Cohort', csvColumns, rows);
  };

  const totalPages = data ? Math.ceil(data.total_count / pageSize) : 0;

  if (loading && !data) return <LoadingState label="Loading invoice cohort…" />;
  if (error) return <ErrorState message={error} />;
  if (!data || data.invoices.length === 0) return <EmptyState label="No invoices match the current filters." />;

  return (
    <div style={billingStyles.card}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
        <h3 style={{ fontSize: '15px', fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>
          Invoice Cohort ({data.total_count.toLocaleString()} AI cohort claims)
        </h3>
        <div title="Final AI Result is the terminal disposition of the AI workflow: Saved to RH, AI Output Rejected, Execution Failed, Stuck, In Progress, Not Required, or Unknown." style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
          Saved to RH · AI Output Rejected · Execution Failed · Stuck · In Progress · Not Required · Unknown
        </div>
        <button
          onClick={handleExport}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
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
          <Download size={14} />
          Export CSV
        </button>
      </div>

      <div
        title="This table contains AI cohort claims in the selected period. Eligibility may be unknown when configuration is unavailable. Business outcome, AI processing status, and writeback are separate lifecycle measures."
        style={{ marginBottom: '12px', fontSize: '12px', color: 'var(--text-muted)' }}
      >
        AI cohort claims only. Eligibility-unknown claims may be included when configuration is unavailable. Writeback describes whether AI line items were saved to RecoveryHub; it does not mean the invoice was released.
      </div>
      {filters.ai_result && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            marginBottom: '12px',
            padding: '8px 12px',
            backgroundColor: 'rgba(59, 130, 246, 0.1)',
            border: '1px solid rgba(59, 130, 246, 0.3)',
            borderRadius: 'var(--border-radius-md)',
            fontSize: '12px',
            color: 'var(--text-primary)',
          }}
        >
          <span>
            Showing final AI result:{' '}
            <strong>{AI_RESULT_LABELS[filters.ai_result as AiResult] ?? filters.ai_result}</strong>
          </span>
          {onClearResultFilter && (
            <button
              type="button"
              onClick={onClearResultFilter}
              style={{
                padding: '2px 10px',
                fontSize: '11px',
                fontWeight: 600,
                border: '1px solid var(--border-color)',
                borderRadius: 'var(--border-radius-md)',
                backgroundColor: 'var(--bg-primary)',
                color: 'var(--text-secondary)',
                cursor: 'pointer',
              }}
            >
              Clear
            </button>
          )}
        </div>
      )}
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              {columns.map((col) => (
                <th key={col} style={tableHeaderStyle}>
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.invoices.map((inv) => (
              <tr
                key={inv.claim_id}
                onClick={() => onRowClick?.(inv.claim_id)}
                style={{
                  cursor: onRowClick ? 'pointer' : 'default',
                  transition: 'background-color 0.15s',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)')}
                onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
              >
                <td style={tableCellStyle}>{inv.claim_id}</td>
                <td style={tableCellStyle}>{inv.department_name || '—'}</td>
                <td style={tableCellStyle}>{inv.run_number || '—'}</td>
                <td style={tableCellStyle}>
                  <span title="Business outcome is the invoice disposition: released, cancelled/rejected, pending, or unknown." style={outcomeBadgeStyle(inv.business_outcome)}>
                    {inv.business_outcome.replace('_', ' ')}
                  </span>
                </td>
                <td style={tableCellStyle}>
                  <span
                    title={
                      inv.ai_result === 'ai_output_rejected'
                        ? 'AI finished but its output was not accepted for writeback.'
                        : inv.ai_result === 'stuck'
                          ? 'No AI-side update beyond the inactivity threshold.'
                          : undefined
                    }
                    style={aiResultBadgeStyle(inv.ai_result)}
                  >
                    {AI_RESULT_LABELS[inv.ai_result] ?? inv.ai_result}
                  </span>
                </td>
                <td style={{ ...tableCellStyle, maxWidth: '260px', whiteSpace: 'normal' }}>
                  <ReasonCell inv={inv} />
                </td>
                <td style={tableCellStyle}>{inv.ai_line_item_count}</td>
                <td style={tableCellStyle}>
                  {inv.confidence !== null ? `${inv.confidence}%` : '—'}
                </td>
                <td style={tableCellStyle}>
                  {inv.invoice_total !== null ? formatCurrency(inv.invoice_total) : '—'}
                </td>
                <td style={{ ...tableCellStyle, color: 'var(--text-muted)', fontSize: '12px' }}>
                  {inv.ai_updated_at
                    ? new Date(inv.ai_updated_at).toLocaleString()
                    : '—'}
                </td>
                <td style={{ ...tableCellStyle, color: 'var(--text-muted)', fontSize: '12px' }}>
                  {inv.ai_result === 'stuck' || inv.ai_result === 'in_progress'
                    ? formatProcessingAge(inv.processing_age_seconds)
                    : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginTop: '16px',
          paddingTop: '16px',
          borderTop: '1px solid var(--border-color)',
        }}
      >
        <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
          Page {page} of {totalPages} ({data.invoices.length} on this page)
        </span>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            disabled={page <= 1}
            onClick={() => setPage(page - 1)}
            style={{
              display: 'flex',
              alignItems: 'center',
              padding: '6px 10px',
              border: '1px solid var(--border-color)',
              borderRadius: 'var(--border-radius-md)',
              backgroundColor: page <= 1 ? 'transparent' : 'var(--bg-primary)',
              color: page <= 1 ? 'var(--text-muted)' : 'var(--text-primary)',
              cursor: page <= 1 ? 'not-allowed' : 'pointer',
              fontSize: '12px',
            }}
          >
            <ChevronLeft size={14} />
          </button>
          <button
            disabled={page >= totalPages}
            onClick={() => setPage(page + 1)}
            style={{
              display: 'flex',
              alignItems: 'center',
              padding: '6px 10px',
              border: '1px solid var(--border-color)',
              borderRadius: 'var(--border-radius-md)',
              backgroundColor: page >= totalPages ? 'transparent' : 'var(--bg-primary)',
              color: page >= totalPages ? 'var(--text-muted)' : 'var(--text-primary)',
              cursor: page >= totalPages ? 'not-allowed' : 'pointer',
              fontSize: '12px',
            }}
          >
            <ChevronRight size={14} />
          </button>
        </div>
      </div>
    </div>
  );
};
