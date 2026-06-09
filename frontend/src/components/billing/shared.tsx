import React from 'react';
import { Loader2, AlertCircle, Inbox } from 'lucide-react';

/** Formats a number as a USD-style currency string. */
export function formatCurrency(value: number | null | undefined, currency = 'USD'): string {
  const amount = typeof value === 'number' && Number.isFinite(value) ? value : 0;
  try {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(amount);
  } catch {
    return `$${amount.toFixed(2)}`;
  }
}

/** Formats a fractional value (e.g. 0.42) or percentage number for display. */
export function formatPercent(value: number | null | undefined, digits = 1): string {
  const v = typeof value === 'number' && Number.isFinite(value) ? value : 0;
  return `${v.toFixed(digits)}%`;
}

/** Maps a budget/forecast utilization percentage to a status color CSS variable. */
export function utilizationColor(pct: number): string {
  if (pct > 100) return '#b91c1c';
  if (pct > 90) return 'var(--color-danger)';
  if (pct > 70) return 'var(--color-warning)';
  return 'var(--color-success)';
}

export const billingStyles: Record<string, React.CSSProperties> = {
  page: {
    display: 'flex',
    flexDirection: 'column',
    gap: '24px',
  },
  sectionTitle: {
    fontSize: '15px',
    fontWeight: 700,
    color: 'var(--text-primary)',
    margin: 0,
  },
  card: {
    backgroundColor: 'var(--bg-secondary)',
    border: '1px solid var(--border-color)',
    borderRadius: 'var(--border-radius-lg)',
    padding: '20px',
    boxShadow: 'var(--shadow-card)',
  },
  loading: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '10px',
    padding: '48px',
    color: 'var(--text-secondary)',
    fontSize: '14px',
  },
  error: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
    padding: '16px 20px',
    backgroundColor: 'rgba(239, 68, 68, 0.1)',
    border: '1px solid rgba(239, 68, 68, 0.2)',
    borderRadius: 'var(--border-radius-md)',
    color: 'var(--color-danger)',
    fontSize: '14px',
    fontWeight: 500,
  },
  empty: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '10px',
    padding: '48px',
    color: 'var(--text-muted)',
    fontSize: '14px',
  },
};

export const LoadingState: React.FC<{ label?: string }> = ({ label = 'Loading…' }) => (
  <div style={billingStyles.loading}>
    <Loader2 size={20} className="loader" style={{ color: 'var(--accent-primary)' }} />
    <span>{label}</span>
  </div>
);

export const ErrorState: React.FC<{ message: string }> = ({ message }) => (
  <div style={billingStyles.error}>
    <AlertCircle size={18} />
    <span><strong>Error:</strong> {message}</span>
  </div>
);

export const EmptyState: React.FC<{ label?: string }> = ({ label = 'No data available.' }) => (
  <div style={billingStyles.empty}>
    <Inbox size={32} style={{ color: 'var(--text-muted)' }} />
    <span>{label}</span>
  </div>
);

/** Returns the current billing period as "YYYY-MM" in UTC. */
export function currentPeriod(): string {
  const now = new Date();
  const year = now.getUTCFullYear();
  const month = String(now.getUTCMonth() + 1).padStart(2, '0');
  return `${year}-${month}`;
}
