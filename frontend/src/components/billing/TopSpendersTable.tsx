import React, { useEffect, useState } from 'react';
import { TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { billingApi, CostSummaryItem } from '../../services/billingApi';
import {
  billingStyles,
  formatCurrency,
  formatPercent,
  currentPeriod,
  LoadingState,
  ErrorState,
  EmptyState,
} from './shared';

interface TopSpendersTableProps {
  period?: string;
  dimension?: string;
  limit?: number;
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

export const TopSpendersTable: React.FC<TopSpendersTableProps> = ({
  period = currentPeriod(),
  dimension = 'ServiceName',
  limit = 15,
}) => {
  const [rows, setRows] = useState<CostSummaryItem[] | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setIsLoading(true);
    billingApi
      .getTopSpenders(period, dimension, limit)
      .then((d) => active && setRows(d))
      .catch((err) => active && setError(err.message))
      .finally(() => active && setIsLoading(false));
    return () => {
      active = false;
    };
  }, [period, dimension, limit]);

  if (isLoading) return <LoadingState label="Loading top spenders…" />;
  if (error) return <ErrorState message={error} />;
  if (!rows || rows.length === 0) return <EmptyState label="No spending data for this period." />;

  const total = rows.reduce((sum, r) => sum + r.total_cost, 0);
  const currency = rows[0]?.currency || 'USD';

  return (
    <div style={billingStyles.card}>
      <h3 style={{ ...billingStyles.sectionTitle, marginBottom: '16px' }}>
        Top Spenders · {period}
      </h3>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ ...thStyle, width: '48px' }}>#</th>
              <th style={thStyle}>{dimension === 'ServiceName' ? 'Service' : 'Resource Group'}</th>
              <th style={{ ...thStyle, textAlign: 'right' }}>Monthly Cost</th>
              <th style={thStyle}>% of Total</th>
              <th style={{ ...thStyle, textAlign: 'right' }}>vs Prior</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => {
              const pct = total > 0 ? (row.total_cost / total) * 100 : 0;
              const change = row.change_pct;
              const changeUp = (change ?? 0) > 0;
              const changeFlat = change === null || change === 0;
              return (
                <tr key={`${row.dimension_value}-${i}`}>
                  <td style={{ ...tdStyle, color: 'var(--text-muted)' }}>{i + 1}</td>
                  <td style={{ ...tdStyle, fontWeight: 600 }}>{row.dimension_value}</td>
                  <td style={{ ...tdStyle, textAlign: 'right' }}>{formatCurrency(row.total_cost, currency)}</td>
                  <td style={tdStyle}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <div
                        style={{
                          flex: 1,
                          height: '6px',
                          borderRadius: '3px',
                          backgroundColor: 'var(--bg-tertiary)',
                          overflow: 'hidden',
                          minWidth: '60px',
                        }}
                      >
                        <div
                          style={{
                            height: '100%',
                            width: `${pct}%`,
                            backgroundColor: 'var(--accent-primary)',
                          }}
                        />
                      </div>
                      <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{formatPercent(pct)}</span>
                    </div>
                  </td>
                  <td style={{ ...tdStyle, textAlign: 'right' }}>
                    <span
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '4px',
                        color: changeFlat
                          ? 'var(--text-muted)'
                          : changeUp
                            ? 'var(--color-danger)'
                            : 'var(--color-success)',
                      }}
                    >
                      {changeFlat ? <Minus size={13} /> : changeUp ? <TrendingUp size={13} /> : <TrendingDown size={13} />}
                      {changeFlat ? '—' : formatPercent(Math.abs(change as number))}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};
