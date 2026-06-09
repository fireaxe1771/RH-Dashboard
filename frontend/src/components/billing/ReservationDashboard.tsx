import React, { useEffect, useMemo, useState } from 'react';
import { Clock } from 'lucide-react';
import { billingApi, ReservationRecommendation } from '../../services/billingApi';
import {
  billingStyles,
  formatCurrency,
  LoadingState,
  ErrorState,
  EmptyState,
} from './shared';

const TERMS = ['P1Y', 'P3Y'] as const;
const TERM_LABELS: Record<string, string> = { P1Y: '1-Year', P3Y: '3-Year' };

const tabStyle = (active: boolean): React.CSSProperties => ({
  padding: '6px 16px',
  fontSize: '13px',
  fontWeight: 600,
  borderRadius: 'var(--border-radius-md)',
  border: '1px solid var(--border-color)',
  backgroundColor: active ? 'var(--accent-primary)' : 'transparent',
  color: active ? '#fff' : 'var(--text-secondary)',
  cursor: 'pointer',
});

function paybackMonths(rec: ReservationRecommendation): number | null {
  const monthlySavings = rec.net_savings;
  if (!monthlySavings || monthlySavings <= 0) return null;
  // Approximate upfront delta as the RI cost; payback measured against monthly savings.
  const upfront = rec.total_cost_with_ri;
  if (!upfront) return null;
  return Math.round(upfront / monthlySavings);
}

export const ReservationDashboard: React.FC = () => {
  const [recs, setRecs] = useState<ReservationRecommendation[] | null>(null);
  const [term, setTerm] = useState<string>('P1Y');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    billingApi
      .getReservationRecommendations()
      .then((d) => active && setRecs(d))
      .catch((err) => active && setError(err.message))
      .finally(() => active && setIsLoading(false));
    return () => {
      active = false;
    };
  }, []);

  const filtered = useMemo(() => {
    if (!recs) return [];
    return recs
      .filter((r) => (r.term || 'P1Y') === term)
      .sort((a, b) => b.net_savings - a.net_savings);
  }, [recs, term]);

  if (isLoading) return <LoadingState label="Loading reservation opportunities…" />;
  if (error) return <ErrorState message={error} />;

  return (
    <div style={billingStyles.page}>
      <div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '12px', marginBottom: '16px' }}>
          <h3 style={billingStyles.sectionTitle}>Purchase Opportunities</h3>
          <div style={{ display: 'flex', gap: '8px' }}>
            {TERMS.map((t) => (
              <button key={t} style={tabStyle(term === t)} onClick={() => setTerm(t)}>
                {TERM_LABELS[t]}
              </button>
            ))}
          </div>
        </div>

        {filtered.length === 0 ? (
          <EmptyState label={`No ${TERM_LABELS[term]} reservation recommendations.`} />
        ) : (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
              gap: '16px',
            }}
          >
            {filtered.map((rec, i) => {
              const payback = paybackMonths(rec);
              return (
                <div key={`${rec.sku_name}-${i}`} style={{ ...billingStyles.card, display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Clock size={16} style={{ color: 'var(--accent-primary)' }} />
                    <span style={{ fontWeight: 700, color: 'var(--text-primary)', fontSize: '14px' }}>{rec.sku_name}</span>
                  </div>
                  <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{rec.location}</span>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', color: 'var(--text-secondary)' }}>
                    <span>Recommended qty</span>
                    <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{rec.recommended_quantity}</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', color: 'var(--text-secondary)' }}>
                    <span>Net savings</span>
                    <span style={{ fontWeight: 700, color: 'var(--color-success)' }}>
                      {formatCurrency(rec.net_savings, rec.currency || 'USD')}
                    </span>
                  </div>
                  {payback != null && (
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', color: 'var(--text-secondary)' }}>
                      <span>Payback</span>
                      <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>~{payback} mo</span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div style={{ ...billingStyles.card, color: 'var(--text-muted)', fontSize: '13px', borderStyle: 'dashed' }}>
        Utilization details coming in a future release.
      </div>
    </div>
  );
};
