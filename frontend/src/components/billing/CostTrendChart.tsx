import React, { useEffect, useState } from 'react';
import { billingApi, CostTrendPoint } from '../../services/billingApi';
import { billingStyles, formatCurrency, LoadingState, ErrorState, EmptyState } from './shared';

interface CostTrendChartProps {
  months?: number;
  dimension?: string;
  title?: string;
}

const SERIES_COLORS = [
  'var(--accent-primary)',
  'var(--color-success)',
  'var(--color-warning)',
  'var(--color-info)',
  '#a855f7',
  '#64748b',
];

interface StackSegment {
  name: string;
  value: number;
  color: string;
}

interface MonthColumn {
  period: string;
  total: number;
  segments: StackSegment[];
}

function buildColumns(points: CostTrendPoint[]): MonthColumn[] {
  const periods = Array.from(new Set(points.map((p) => p.period))).sort();

  // Determine the top-5 services by total cost across the whole window.
  const totalsByService = new Map<string, number>();
  for (const p of points) {
    totalsByService.set(p.dimension_value, (totalsByService.get(p.dimension_value) || 0) + p.total_cost);
  }
  const topServices = Array.from(totalsByService.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5)
    .map(([name]) => name);

  return periods.map((period) => {
    const monthPoints = points.filter((p) => p.period === period);
    const segments: StackSegment[] = [];
    let otherTotal = 0;
    for (const p of monthPoints) {
      const idx = topServices.indexOf(p.dimension_value);
      if (idx >= 0) {
        segments.push({ name: p.dimension_value, value: p.total_cost, color: SERIES_COLORS[idx] });
      } else {
        otherTotal += p.total_cost;
      }
    }
    if (otherTotal > 0) {
      segments.push({ name: 'Other', value: otherTotal, color: SERIES_COLORS[5] });
    }
    const total = segments.reduce((sum, s) => sum + s.value, 0);
    return { period, total, segments };
  });
}

export const CostTrendChart: React.FC<CostTrendChartProps> = ({
  months = 12,
  dimension = 'ServiceName',
  title = 'Cost Trend',
}) => {
  const [data, setData] = useState<CostTrendPoint[] | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setIsLoading(true);
    billingApi
      .getCostTrend(months, dimension)
      .then((d) => active && setData(d))
      .catch((err) => active && setError(err.message))
      .finally(() => active && setIsLoading(false));
    return () => {
      active = false;
    };
  }, [months, dimension]);

  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState message={error} />;
  if (!data || data.length === 0) return <EmptyState label="No cost trend data yet." />;

  const columns = buildColumns(data);
  const maxTotal = Math.max(...columns.map((c) => c.total), 1);

  const svgWidth = 720;
  const svgHeight = 300;
  const padding = { top: 20, right: 20, bottom: 40, left: 70 };
  const plotWidth = svgWidth - padding.left - padding.right;
  const plotHeight = svgHeight - padding.top - padding.bottom;
  const barWidth = Math.min(48, (plotWidth / columns.length) * 0.6);
  const slot = plotWidth / columns.length;

  const legendEntries = Array.from(
    new Map(columns.flatMap((c) => c.segments).map((s) => [s.name, s.color])).entries(),
  );

  return (
    <div style={billingStyles.card}>
      <h3 style={{ ...billingStyles.sectionTitle, marginBottom: '16px' }}>{title}</h3>
      <svg viewBox={`0 0 ${svgWidth} ${svgHeight}`} width="100%" height={svgHeight} role="img" aria-label={title}>
        {[0, 0.25, 0.5, 0.75, 1].map((frac) => {
          const y = padding.top + plotHeight * (1 - frac);
          return (
            <g key={frac}>
              <line
                x1={padding.left}
                y1={y}
                x2={svgWidth - padding.right}
                y2={y}
                stroke="var(--border-color)"
                strokeWidth={1}
              />
              <text x={padding.left - 10} y={y + 4} textAnchor="end" fontSize={11} fill="var(--text-muted)">
                {formatCurrency(maxTotal * frac).replace(/\.00$/, '')}
              </text>
            </g>
          );
        })}

        {columns.map((col, i) => {
          const x = padding.left + slot * i + (slot - barWidth) / 2;
          let yCursor = padding.top + plotHeight;
          return (
            <g key={col.period}>
              {col.segments.map((seg) => {
                const segHeight = (seg.value / maxTotal) * plotHeight;
                yCursor -= segHeight;
                return (
                  <rect
                    key={seg.name}
                    x={x}
                    y={yCursor}
                    width={barWidth}
                    height={Math.max(segHeight, 0)}
                    fill={seg.color}
                  >
                    <title>{`${col.period} · ${seg.name}: ${formatCurrency(seg.value)}`}</title>
                  </rect>
                );
              })}
              <text
                x={x + barWidth / 2}
                y={svgHeight - padding.bottom + 18}
                textAnchor="middle"
                fontSize={11}
                fill="var(--text-muted)"
              >
                {col.period.slice(2)}
              </text>
            </g>
          );
        })}
      </svg>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px', marginTop: '12px' }}>
        {legendEntries.map(([name, color]) => (
          <div key={name} style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--text-secondary)' }}>
            <span style={{ width: '12px', height: '12px', borderRadius: '3px', backgroundColor: color, display: 'inline-block' }} />
            {name}
          </div>
        ))}
      </div>
    </div>
  );
};
