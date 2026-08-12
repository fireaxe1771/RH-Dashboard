import React, { useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, Download } from 'lucide-react';
import { billingStyles, LoadingState, ErrorState, EmptyState, formatCurrency } from '../billing/shared';
import { exportToCsv } from '../../utils/export';
import {
  aiAnalyticsApi,
  AiAnalyticsFilters,
  AiInvoiceListItem,
  AiInvoiceCohortResponse,
} from '../../services/aiAnalyticsApi';

interface Props {
  filters: AiAnalyticsFilters;
  onRowClick?: (claimId: number) => void;
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

const writebackBadgeStyle = (status: string): React.CSSProperties => {
  switch (status) {
    case 'success':
      return { color: '#22c55e' };
    case 'failed_or_not_saved':
      return { color: '#ef4444' };
    case 'not_required':
      return { color: '#94a3b8' };
    case 'pending':
      return { color: '#eab308' };
    default:
      return { color: 'var(--text-muted)' };
  }
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

export const AiInvoiceCohortGrid: React.FC<Props> = ({ filters, onRowClick }) => {
  const [data, setData] = useState<AiInvoiceCohortResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const pageSize = 50;

  React.useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    const fullFilters = { ...filters, page, page_size: pageSize };
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
  }, [filters, page]);

  const columns = useMemo(
    () => [
      'Claim ID',
      'Department',
      'Run #',
      'Business Outcome',
      'Rejection Reason',
      'AI Status',
      'Confidence',
      'Writeback',
      'Invoice Total',
      'Updated',
    ],
    [],
  );

  const handleExport = () => {
    if (!data) return;
    const rows = data.invoices.map((inv) => ({
      'Claim ID': inv.claim_id,
      Department: inv.department_name || '—',
      'Run #': inv.run_number || '—',
      'Business Outcome': inv.business_outcome,
      'Rejection Reason': inv.raw_rejection_reason || '—',
      'AI Status': inv.ai_processing_status || '—',
      Confidence: inv.confidence ?? '—',
      Writeback: inv.writeback_status,
      'Invoice Total': inv.invoice_total ?? 0,
      Updated: inv.ai_business_updated_at || '—',
    }));
    exportToCsv('AI_Invoice_Cohort', columns, rows);
  };

  const totalPages = data ? Math.ceil(data.total_count / pageSize) : 0;

  if (loading && !data) return <LoadingState label="Loading invoice cohort…" />;
  if (error) return <ErrorState message={error} />;
  if (!data || data.invoices.length === 0) return <EmptyState label="No invoices match the current filters." />;

  return (
    <div style={billingStyles.card}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
        <h3 style={{ fontSize: '15px', fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>
          Invoice Cohort ({data.total_count.toLocaleString()} total)
        </h3>
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
                  <span style={outcomeBadgeStyle(inv.business_outcome)}>
                    {inv.business_outcome.replace('_', ' ')}
                  </span>
                </td>
                <td style={{ ...tableCellStyle, maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {inv.raw_rejection_reason || '—'}
                </td>
                <td style={tableCellStyle}>{inv.ai_processing_status || '—'}</td>
                <td style={tableCellStyle}>
                  {inv.confidence !== null ? `${inv.confidence}%` : '—'}
                </td>
                <td style={{ ...tableCellStyle, ...writebackBadgeStyle(inv.writeback_status) }}>
                  {inv.writeback_status.replace(/_/g, ' ')}
                </td>
                <td style={tableCellStyle}>
                  {inv.invoice_total !== null ? formatCurrency(inv.invoice_total) : '—'}
                </td>
                <td style={{ ...tableCellStyle, color: 'var(--text-muted)', fontSize: '12px' }}>
                  {inv.ai_business_updated_at
                    ? new Date(inv.ai_business_updated_at).toLocaleDateString()
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
