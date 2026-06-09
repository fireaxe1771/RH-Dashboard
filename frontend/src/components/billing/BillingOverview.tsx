import React, { useEffect, useState } from 'react';
import {
  DollarSign,
  TrendingUp,
  TrendingDown,
  Target,
  AlertTriangle,
  CheckCircle,
} from 'lucide-react';
import {
  billingApi,
  CostSummaryResponse,
  BudgetItem,
  AdvisorSummary,
} from '../../services/billingApi';
import {
  billingStyles,
  formatCurrency,
  formatPercent,
  utilizationColor,
  currentPeriod,
  LoadingState,
  ErrorState,
} from './shared';
import { CostTrendChart } from './CostTrendChart';

interface OverviewData {
  summary: CostSummaryResponse;
  budgets: BudgetItem[];
  advisor: AdvisorSummary;
}

const kpiCardStyle: React.CSSProperties = {
  ...billingStyles.card,
  display: 'flex',
  flexDirection: 'column',
  gap: '12px',
  minHeight: '120px',
};

const kpiLabelStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: '8px',
  fontSize: '13px',
  color: 'var(--text-secondary)',
  fontWeight: 600,
};

const kpiValueStyle: React.CSSProperties = {
  fontSize: '26px',
  fontWeight: 700,
  color: 'var(--text-primary)',
};

export const BillingOverview: React.FC = () => {
  const [data, setData] = useState<OverviewData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const period = currentPeriod();
    Promise.all([
      billingApi.getCostSummary(period),
      billingApi.getBudgets(),
      billingApi.getAdvisorSummary(),
    ])
      .then(([summary, budgets, advisor]) => {
        if (active) setData({ summary, budgets, advisor });
      })
      .catch((err) => active && setError(err.message))
      .finally(() => active && setIsLoading(false));
    return () => {
      active = false;
    };
  }, []);

  if (isLoading) return <LoadingState label="Loading cost overview…" />;
  if (error) return <ErrorState message={error} />;
  if (!data) return null;

  const { summary, budgets, advisor } = data;
  const topService = summary.items.length > 0 ? summary.items[0] : null;
  const topBudget = budgets.length > 0
    ? [...budgets].sort((a, b) => b.utilization_pct - a.utilization_pct)[0]
    : null;
  const mtdChangePct = topService?.change_pct ?? null;
  const mtdUp = (mtdChangePct ?? 0) >= 0;

  return (
    <div style={billingStyles.page}>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
          gap: '16px',
        }}
      >
        {/* MTD Spend */}
        <div style={kpiCardStyle}>
          <span style={kpiLabelStyle}>
            <DollarSign size={16} style={{ color: 'var(--accent-primary)' }} /> MTD Spend
          </span>
          <span style={kpiValueStyle}>{formatCurrency(summary.total, summary.currency)}</span>
          {mtdChangePct !== null && (
            <span
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                fontSize: '13px',
                color: mtdUp ? 'var(--color-danger)' : 'var(--color-success)',
              }}
            >
              {mtdUp ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
              {formatPercent(Math.abs(mtdChangePct))} vs prior month
            </span>
          )}
        </div>

        {/* Top Service */}
        <div style={kpiCardStyle}>
          <span style={kpiLabelStyle}>
            <TrendingUp size={16} style={{ color: 'var(--color-info)' }} /> Top Service
          </span>
          <span style={kpiValueStyle}>{topService ? topService.dimension_value : '—'}</span>
          <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
            {topService ? formatCurrency(topService.total_cost, topService.currency) : 'No data'}
          </span>
        </div>

        {/* Budget Status */}
        <div style={kpiCardStyle}>
          <span style={kpiLabelStyle}>
            <Target size={16} style={{ color: 'var(--color-warning)' }} /> Budget Status
          </span>
          <span style={kpiValueStyle}>
            {topBudget ? formatPercent(topBudget.utilization_pct) : '—'}
          </span>
          {topBudget ? (
            <div>
              <div
                style={{
                  height: '8px',
                  borderRadius: '4px',
                  backgroundColor: 'var(--bg-tertiary)',
                  overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    height: '100%',
                    width: `${Math.min(topBudget.utilization_pct, 100)}%`,
                    backgroundColor: utilizationColor(topBudget.utilization_pct),
                  }}
                />
              </div>
              <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{topBudget.budget_name}</span>
            </div>
          ) : (
            <span style={{ fontSize: '13px', color: 'var(--text-muted)' }}>No budgets configured</span>
          )}
        </div>

        {/* Cost Savings Available */}
        <div style={kpiCardStyle}>
          <span style={kpiLabelStyle}>
            {advisor.total_monthly_savings > 0 ? (
              <AlertTriangle size={16} style={{ color: 'var(--color-success)' }} />
            ) : (
              <CheckCircle size={16} style={{ color: 'var(--color-success)' }} />
            )}
            Cost Savings Available
          </span>
          <span style={{ ...kpiValueStyle, color: 'var(--color-success)' }}>
            {formatCurrency(advisor.total_monthly_savings, advisor.currency)}
          </span>
          <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
            {advisor.cost_recommendations} cost recommendation{advisor.cost_recommendations === 1 ? '' : 's'}/mo
          </span>
        </div>
      </div>

      <CostTrendChart title="12-Month Cost Trend" />
    </div>
  );
};
