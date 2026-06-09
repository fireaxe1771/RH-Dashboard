import React, { useEffect, useState } from 'react';
import { Bell } from 'lucide-react';
import { billingApi, BudgetItem, AlertItem } from '../../services/billingApi';
import {
  billingStyles,
  formatCurrency,
  LoadingState,
  ErrorState,
  EmptyState,
} from './shared';
import { BudgetCard } from './BudgetCard';

function alertColor(status: string): string {
  const s = status.toLowerCase();
  if (s.includes('past') || s.includes('exceed')) return 'var(--color-danger)';
  if (s.includes('active') || s.includes('triggered')) return 'var(--color-warning)';
  return 'var(--color-info)';
}

export const BudgetsPanel: React.FC = () => {
  const [budgets, setBudgets] = useState<BudgetItem[] | null>(null);
  const [alerts, setAlerts] = useState<AlertItem[] | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([billingApi.getBudgets(), billingApi.getAlerts()])
      .then(([b, a]) => {
        if (active) {
          setBudgets(b);
          setAlerts(a);
        }
      })
      .catch((err) => active && setError(err.message))
      .finally(() => active && setIsLoading(false));
    return () => {
      active = false;
    };
  }, []);

  if (isLoading) return <LoadingState label="Loading budgets & alerts…" />;
  if (error) return <ErrorState message={error} />;

  const sortedBudgets = budgets ? [...budgets].sort((a, b) => b.utilization_pct - a.utilization_pct) : [];

  return (
    <div style={billingStyles.page}>
      <div>
        <h3 style={{ ...billingStyles.sectionTitle, marginBottom: '16px' }}>Budgets</h3>
        {sortedBudgets.length === 0 ? (
          <EmptyState label="No budgets configured." />
        ) : (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
              gap: '16px',
            }}
          >
            {sortedBudgets.map((b) => (
              <BudgetCard key={b.budget_id} budget={b} />
            ))}
          </div>
        )}
      </div>

      <div>
        <h3 style={{ ...billingStyles.sectionTitle, marginBottom: '16px' }}>Active Alerts</h3>
        {!alerts || alerts.length === 0 ? (
          <EmptyState label="No active cost alerts." />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {alerts.map((alert) => (
              <div
                key={alert.alert_id}
                style={{
                  ...billingStyles.card,
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: '12px',
                  borderLeft: `3px solid ${alertColor(alert.status)}`,
                }}
              >
                <Bell size={18} style={{ color: alertColor(alert.status), flexShrink: 0, marginTop: '2px' }} />
                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                  <span style={{ fontWeight: 600, color: 'var(--text-primary)', fontSize: '14px' }}>
                    {alert.alert_name || alert.alert_type}
                  </span>
                  <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>{alert.description}</span>
                  {alert.threshold != null && alert.current_spend != null && (
                    <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                      {formatCurrency(alert.current_spend, alert.currency || 'USD')} of{' '}
                      {formatCurrency(alert.threshold, alert.currency || 'USD')} threshold
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
