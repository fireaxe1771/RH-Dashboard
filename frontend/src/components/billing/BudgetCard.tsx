import React from 'react';
import { TrendingUp, TrendingDown, AlertTriangle } from 'lucide-react';
import { BudgetItem } from '../../services/billingApi';
import { billingStyles, formatCurrency, formatPercent, utilizationColor } from './shared';

interface BudgetCardProps {
  budget: BudgetItem;
}

const badgeStyle: React.CSSProperties = {
  fontSize: '11px',
  fontWeight: 600,
  padding: '2px 8px',
  borderRadius: '999px',
  backgroundColor: 'var(--bg-tertiary)',
  color: 'var(--text-secondary)',
};

export const BudgetCard: React.FC<BudgetCardProps> = ({ budget }) => {
  const currency = budget.currency || budget.current_spend_currency || 'USD';
  const util = budget.utilization_pct;
  const color = utilizationColor(util);
  const forecastUp = (budget.forecast_spend ?? 0) >= budget.current_spend;

  return (
    <div style={{ ...billingStyles.card, display: 'flex', flexDirection: 'column', gap: '10px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
        <span style={{ fontSize: '15px', fontWeight: 700, color: 'var(--text-primary)' }}>
          {budget.budget_name}
        </span>
        {util > 80 && <AlertTriangle size={16} style={{ color }} />}
      </div>
      <span style={{ fontSize: '12px', color: 'var(--text-muted)', wordBreak: 'break-all' }}>{budget.scope}</span>

      <div
        style={{
          height: '10px',
          borderRadius: '5px',
          backgroundColor: 'var(--bg-tertiary)',
          overflow: 'hidden',
        }}
      >
        <div style={{ height: '100%', width: `${Math.min(util, 100)}%`, backgroundColor: color }} />
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
        <span style={{ color: 'var(--text-secondary)' }}>
          {formatCurrency(budget.current_spend, currency)} / {formatCurrency(budget.amount, currency)}
        </span>
        <span style={{ fontWeight: 600, color }}>{formatPercent(util)}</span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
        <span style={badgeStyle}>{budget.time_grain || 'Monthly'}</span>
        {budget.forecast_spend != null && (
          <span
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              fontSize: '12px',
              color: forecastUp ? 'var(--color-warning)' : 'var(--text-secondary)',
            }}
          >
            {forecastUp ? <TrendingUp size={13} /> : <TrendingDown size={13} />}
            Forecast {formatCurrency(budget.forecast_spend, currency)}
          </span>
        )}
      </div>
    </div>
  );
};
