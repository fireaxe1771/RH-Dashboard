import React, { useEffect, useState } from 'react';
import { Lightbulb, ChevronDown, ChevronUp } from 'lucide-react';
import {
  billingApi,
  AdvisorRecommendation,
  AdvisorSummary,
} from '../../services/billingApi';
import {
  billingStyles,
  formatCurrency,
  LoadingState,
  ErrorState,
  EmptyState,
} from './shared';

const CATEGORIES = ['All', 'Cost', 'Security', 'Performance', 'HighAvailability', 'OperationalExcellence'];
const IMPACTS = ['All', 'High', 'Medium', 'Low'];

function impactColor(impact: string): string {
  if (impact === 'High') return 'var(--color-danger)';
  if (impact === 'Medium') return 'var(--color-warning)';
  return 'var(--color-info)';
}

const tabStyle = (active: boolean): React.CSSProperties => ({
  padding: '6px 14px',
  fontSize: '13px',
  fontWeight: 600,
  borderRadius: 'var(--border-radius-md)',
  border: '1px solid var(--border-color)',
  backgroundColor: active ? 'var(--accent-primary)' : 'transparent',
  color: active ? '#fff' : 'var(--text-secondary)',
  cursor: 'pointer',
});

const badge = (color: string): React.CSSProperties => ({
  fontSize: '11px',
  fontWeight: 600,
  padding: '2px 8px',
  borderRadius: '999px',
  backgroundColor: 'var(--bg-tertiary)',
  color,
});

export const AdvisorPanel: React.FC = () => {
  const [summary, setSummary] = useState<AdvisorSummary | null>(null);
  const [recs, setRecs] = useState<AdvisorRecommendation[] | null>(null);
  const [category, setCategory] = useState('All');
  const [impact, setImpact] = useState('All');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    billingApi
      .getAdvisorSummary()
      .then((s) => active && setSummary(s))
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    setIsLoading(true);
    setError(null);
    billingApi
      .getAdvisorRecommendations(
        category === 'All' ? undefined : category,
        impact === 'All' ? undefined : impact,
      )
      .then((d) => active && setRecs(d))
      .catch((err) => active && setError(err.message))
      .finally(() => active && setIsLoading(false));
    return () => {
      active = false;
    };
  }, [category, impact]);

  return (
    <div style={billingStyles.page}>
      <div style={{ ...billingStyles.card, display: 'flex', alignItems: 'center', gap: '12px' }}>
        <Lightbulb size={22} style={{ color: 'var(--color-warning)' }} />
        <span style={{ fontSize: '15px', fontWeight: 700, color: 'var(--text-primary)' }}>
          {summary
            ? `${summary.total_recommendations} recommendations · ${formatCurrency(summary.total_monthly_savings, summary.currency)}/month potential savings`
            : 'Azure Advisor Recommendations'}
        </span>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
        {CATEGORIES.map((c) => (
          <button key={c} style={tabStyle(category === c)} onClick={() => setCategory(c)}>
            {c}
          </button>
        ))}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
        {IMPACTS.map((i) => (
          <button key={i} style={tabStyle(impact === i)} onClick={() => setImpact(i)}>
            {i} Impact
          </button>
        ))}
      </div>

      {isLoading ? (
        <LoadingState label="Loading recommendations…" />
      ) : error ? (
        <ErrorState message={error} />
      ) : !recs || recs.length === 0 ? (
        <EmptyState label="No recommendations match the selected filters." />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {recs.map((rec) => {
            const isOpen = expanded === rec.recommendation_id;
            const problem = rec.problem_description || '';
            const preview = problem.length > 120 ? `${problem.slice(0, 120)}…` : problem;
            return (
              <div key={rec.recommendation_id} style={billingStyles.card}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                  <span style={badge(impactColor(rec.impact))}>{rec.impact}</span>
                  <span style={badge('var(--text-secondary)')}>{rec.category}</span>
                  <span style={{ fontWeight: 600, color: 'var(--text-primary)', fontSize: '14px' }}>
                    {rec.impacted_value}
                  </span>
                  {rec.resource_group && (
                    <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>· {rec.resource_group}</span>
                  )}
                  {rec.estimated_monthly_savings != null && (
                    <span style={{ marginLeft: 'auto', fontWeight: 700, color: 'var(--color-success)' }}>
                      {formatCurrency(rec.estimated_monthly_savings, rec.savings_currency || 'USD')}/mo
                    </span>
                  )}
                </div>
                <p style={{ fontSize: '13px', color: 'var(--text-secondary)', margin: '10px 0 0 0' }}>
                  {isOpen ? problem : preview}
                </p>
                {isOpen && (
                  <p style={{ fontSize: '13px', color: 'var(--text-primary)', margin: '8px 0 0 0' }}>
                    <strong>Recommended action:</strong> {rec.solution_description}
                  </p>
                )}
                <button
                  onClick={() => setExpanded(isOpen ? null : rec.recommendation_id)}
                  style={{
                    marginTop: '10px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px',
                    background: 'none',
                    border: 'none',
                    color: 'var(--accent-primary)',
                    cursor: 'pointer',
                    fontSize: '13px',
                    fontWeight: 600,
                    padding: 0,
                  }}
                >
                  {isOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                  {isOpen ? 'Hide Details' : 'View Details'}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
