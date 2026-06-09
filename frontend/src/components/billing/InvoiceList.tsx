import React, { useEffect, useState } from 'react';
import { Download } from 'lucide-react';
import { billingApi, InvoiceItem } from '../../services/billingApi';
import {
  billingStyles,
  formatCurrency,
  LoadingState,
  ErrorState,
  EmptyState,
} from './shared';

function statusColor(status: string): string {
  const s = status.toLowerCase();
  if (s === 'paid') return 'var(--color-success)';
  if (s === 'pastdue' || s === 'past due') return 'var(--color-danger)';
  if (s === 'void') return 'var(--text-muted)';
  return 'var(--color-info)';
}

const thStyle: React.CSSProperties = {
  textAlign: 'left',
  padding: '10px 12px',
  fontSize: '12px',
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
  color: 'var(--text-muted)',
  borderBottom: '1px solid var(--border-color)',
};

const tdStyle: React.CSSProperties = {
  padding: '12px',
  fontSize: '13px',
  color: 'var(--text-primary)',
  borderBottom: '1px solid var(--border-color)',
};

export const InvoiceList: React.FC = () => {
  const [invoices, setInvoices] = useState<InvoiceItem[] | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    billingApi
      .getInvoices()
      .then((d) => active && setInvoices(d))
      .catch((err) => active && setError(err.message))
      .finally(() => active && setIsLoading(false));
    return () => {
      active = false;
    };
  }, []);

  if (isLoading) return <LoadingState label="Loading invoices…" />;
  if (error) return <ErrorState message={error} />;
  if (!invoices || invoices.length === 0) return <EmptyState label="No invoices available." />;

  const sorted = [...invoices].sort((a, b) =>
    (b.billing_period_start || '').localeCompare(a.billing_period_start || ''),
  );

  return (
    <div style={billingStyles.card}>
      <h3 style={{ ...billingStyles.sectionTitle, marginBottom: '16px' }}>Invoices</h3>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={thStyle}>Invoice ID</th>
              <th style={thStyle}>Billing Period</th>
              <th style={thStyle}>Invoice Date</th>
              <th style={{ ...thStyle, textAlign: 'right' }}>Amount</th>
              <th style={{ ...thStyle, textAlign: 'right' }}>Amount Due</th>
              <th style={thStyle}>Status</th>
              <th style={thStyle}>PDF</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((inv) => (
              <tr key={inv.invoice_id}>
                <td style={{ ...tdStyle, fontWeight: 600 }}>{inv.invoice_id}</td>
                <td style={tdStyle}>
                  {inv.billing_period_start}
                  {inv.billing_period_end ? ` – ${inv.billing_period_end}` : ''}
                </td>
                <td style={tdStyle}>{inv.invoice_date}</td>
                <td style={{ ...tdStyle, textAlign: 'right' }}>
                  {formatCurrency(inv.billed_amount, inv.billing_currency)}
                </td>
                <td style={{ ...tdStyle, textAlign: 'right' }}>
                  {formatCurrency(inv.amount_due, inv.billing_currency)}
                </td>
                <td style={tdStyle}>
                  <span
                    style={{
                      fontSize: '11px',
                      fontWeight: 600,
                      padding: '2px 10px',
                      borderRadius: '999px',
                      color: statusColor(inv.status),
                      backgroundColor: 'var(--bg-tertiary)',
                    }}
                  >
                    {inv.status || 'Unknown'}
                  </span>
                </td>
                <td style={tdStyle}>
                  {inv.invoice_download_url ? (
                    <a
                      href={inv.invoice_download_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ color: 'var(--accent-primary)', display: 'inline-flex', alignItems: 'center', gap: '4px' }}
                    >
                      <Download size={14} /> PDF
                    </a>
                  ) : (
                    <span style={{ color: 'var(--text-muted)' }}>—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};
