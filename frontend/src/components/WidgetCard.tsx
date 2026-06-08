import React, { useEffect, useState } from 'react';
import { api, Widget, QueryResult } from '../services/api';
import { DashboardFilters } from './FilterBar';
import { 
  TrendingUp, 
  TrendingDown, 
  Table, 
  ExternalLink, 
  AlertCircle,
  BarChart3,
  LineChart,
  PieChart
} from 'lucide-react';

interface WidgetCardProps {
  widget: Widget;
  filters: DashboardFilters;
  onDrillDown?: (fieldName: string, value: unknown, title: string) => void;
}

export const WidgetCard: React.FC<WidgetCardProps> = ({ widget, filters, onDrillDown }) => {
  const [data, setData] = useState<QueryResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const isLooker = widget.type === 'looker';

  useEffect(() => {
    if (isLooker) {
      setLoading(false);
      return;
    }

    let active = true;
    setLoading(true);
    setError(null);

    // If no query is provided, show placeholder
    if (!widget.sql_query) {
      setLoading(false);
      setError("No SQL query defined for this widget.");
      return;
    }

    api.runSqlQuery(widget.sql_query, filters)
      .then((result) => {
        if (active) {
          setData(result);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (active) {
          console.error(`Error loading widget "${widget.title}":`, err);
          setError(err.message || "Failed to load widget data.");
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [widget.sql_query, widget.type, filters, isLooker]);

  // Helper: Format values based on column/widget preferences
  const formatValue = (val: unknown, format?: string): string => {
    if (val === null || val === undefined) return '0';
    if (typeof val === 'number') {
      if (format === 'currency') {
        return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(val);
      }
      if (format === 'percentage') {
        return `${val.toFixed(1)}%`;
      }
      return new Intl.NumberFormat('en-US').format(val);
    }
    return String(val);
  };

  // Helper: Extract label and value from first row for Stat Card
  const getStatData = () => {
    if (!data || data.rows.length === 0) return { value: '0', label: 'No Data' };
    const firstRow = data.rows[0];
    const keys = Object.keys(firstRow);
    const valueKey = keys.find(k => typeof firstRow[k] === 'number') || keys[0];
    return {
      value: formatValue(firstRow[valueKey], widget.config.format),
      label: keys[0] !== valueKey ? String(firstRow[keys[0]]) : ''
    };
  };

  if (loading) {
    return (
      <div className="card" style={{ height: '350px', justifyContent: 'center', alignItems: 'center' }}>
        <div className="loader" />
        <span style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '12px' }}>Running SQL query...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="card" style={{ height: '350px', justifyContent: 'center' }}>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '12px', color: 'var(--color-danger)', textAlign: 'center', padding: '16px' }}>
          <AlertCircle size={32} />
          <span style={{ fontSize: '14px', fontWeight: 600 }}>SQL Execution Failed</span>
          <p style={{ fontSize: '11px', color: 'var(--text-secondary)', fontFamily: 'monospace', wordBreak: 'break-all', maxHeight: '120px', overflowY: 'auto', width: '100%' }}>
            {error}
          </p>
        </div>
      </div>
    );
  }

  // --- RENDERING MODES ---

  // 1. STAT CARD RENDERER
  if (widget.type === 'stat') {
    const { value, label } = getStatData();
    return (
      <div className="card stat-card" style={{ gridColumn: `span ${widget.layout.w}`, minHeight: '150px' }}>
        <div className="stat-header">
          <span>{widget.title}</span>
          <TrendingUp size={16} className="trend-up" />
        </div>
        <div>
          <div className="stat-value">{value}</div>
          {label && (
            <div className="stat-trend trend-up">
              <span>{label}</span>
            </div>
          )}
        </div>
      </div>
    );
  }

  // 2. LOOKER STUDIO / EXTERNAL EMBED RENDERER
  if (widget.type === 'looker') {
    const embedUrl = widget.config.embedUrl || '';
    return (
      <div className="card" style={{ gridColumn: `span ${widget.layout.w}`, height: '450px', padding: '12px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 8px 8px 8px', borderBottom: '1px solid var(--border-color)' }}>
          <span style={{ fontSize: '14px', fontWeight: 600 }}>{widget.title}</span>
          {embedUrl && (
            <a href={embedUrl} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '4px', textDecoration: 'none', fontSize: '12px' }}>
              <span>Open BI</span>
              <ExternalLink size={12} />
            </a>
          )}
        </div>
        {embedUrl ? (
          <iframe
            src={embedUrl}
            title={widget.title}
            style={{ width: '100%', height: '100%', border: 'none', borderRadius: 'var(--border-radius-md)', backgroundColor: '#181d28' }}
            sandbox="allow-storage-access-by-user-activation allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox"
          />
        ) : (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifySelf: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
            No Embed URL specified in config.
          </div>
        )}
      </div>
    );
  }

  // Check if we have valid rows for charts
  const rows = data?.rows || [];
  const columns = data?.columns || [];
  if (rows.length === 0) {
    return (
      <div className="card" style={{ gridColumn: `span ${widget.layout.w}`, height: '350px', justifyContent: 'center', alignItems: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
        <span>No rows returned from SQL target.</span>
      </div>
    );
  }

  // Extract variables from configuration
  const xKey = widget.config.xAxisKey || columns[0];
  const yKeys = widget.config.yAxisKeys || columns.filter(c => c !== xKey).slice(0, 1);
  const colorPalette = widget.config.colors || ['#6366f1', '#10b981', '#f59e0b', '#ef4444', '#0ea5e9'];
  const formatChartTick = (value: number): string => {
    if (!Number.isFinite(value)) return '0';
    return formatValue(Math.round(value));
  };

  // 3. TABLE RENDERER
  if (widget.type === 'table') {
    return (
      <div className="card" style={{ gridColumn: `span ${widget.layout.w}`, height: '380px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: '14px', fontWeight: 600 }}>{widget.title}</span>
          <Table size={14} style={{ color: 'var(--text-muted)' }} />
        </div>
        <div className="table-container" style={{ flex: 1, overflowY: 'auto' }}>
          <table className="data-table">
            <thead>
              <tr>
                {columns.map(col => (
                  <th key={col}>{col}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 50).map((row, index) => (
                <tr key={index}>
                  {columns.map(col => (
                    <td key={col}>{formatValue(row[col])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  // --- SVG CHART GENERATORS ---

  // 4. BAR CHART RENDERER
  if (widget.type === 'bar') {
    const chartHeight = 220;
    const padding = { top: 10, right: 10, bottom: 40, left: 55 };
    const svgWidth = 500;
    const svgHeight = 280;
    
    // Find numeric maximum for Y scaling
    const yMax = Math.max(...rows.map(row => Math.max(...yKeys.map(yk => Number(row[yk]) || 0))), 1) * 1.1;

    const plotWidth = svgWidth - padding.left - padding.right;
    const plotHeight = svgHeight - padding.top - padding.bottom;
    const barWidth = Math.max(10, (plotWidth / rows.length) * 0.6);
    const spacing = (plotWidth / rows.length);

    return (
      <div className="card" style={{ gridColumn: `span ${widget.layout.w}`, height: '350px' }}>
        <span style={{ fontSize: '14px', fontWeight: 600 }}>{widget.title}</span>
        <div className="chart-container">
          <svg className="svg-chart" viewBox={`0 0 ${svgWidth} ${svgHeight}`} width="100%" height="100%">
            {/* Grid lines */}
            {[0, 0.25, 0.5, 0.75, 1].map((ratio, idx) => {
              const yVal = plotHeight * (1 - ratio) + padding.top;
              const labelVal = yMax * ratio;
              return (
                <g key={idx}>
                  <line className="svg-grid-line" x1={padding.left} y1={yVal} x2={svgWidth - padding.right} y2={yVal} />
                  <text className="svg-axis-text" x={padding.left - 10} y={yVal + 4} textAnchor="end">
                    {formatChartTick(labelVal)}
                  </text>
                </g>
              );
            })}

            {/* Bars */}
            {rows.map((row, rowIdx) => {
              const xCenter = padding.left + rowIdx * spacing + spacing / 2;
              const xVal = xCenter - barWidth / 2;
              const xLabel = String(row[xKey]);

              return yKeys.map((yKey, keyIdx) => {
                const val = Number(row[yKey]) || 0;
                const h = (val / yMax) * plotHeight;
                const yVal = plotHeight - h + padding.top;
                const barColor = colorPalette[keyIdx % colorPalette.length];

                return (
                  <g key={`${rowIdx}-${keyIdx}`}>
                    <rect
                      className="svg-bar"
                      x={xVal}
                      y={yVal}
                      width={barWidth}
                      height={Math.max(2, h)}
                      fill={barColor}
                      onClick={() => onDrillDown && onDrillDown(xKey, row[xKey], widget.title)}
                    >
                      <title>{`${xLabel} (${yKey}): ${formatValue(val)}`}</title>
                    </rect>
                    {keyIdx === 0 && (
                      <text
                        className="svg-axis-text"
                        x={xCenter}
                        y={svgHeight - padding.bottom + 18}
                        textAnchor="middle"
                        style={{ fontSize: '9px' }}
                      >
                        {xLabel.length > 8 ? `${xLabel.substring(0, 6)}..` : xLabel}
                      </text>
                    )}
                  </g>
                );
              });
            })}
          </svg>
        </div>
      </div>
    );
  }

  // 5. LINE CHART RENDERER
  if (widget.type === 'line') {
    const svgWidth = 500;
    const svgHeight = 280;
    const padding = { top: 15, right: 15, bottom: 40, left: 55 };

    const yMax = Math.max(...rows.map(row => Math.max(...yKeys.map(yk => Number(row[yk]) || 0))), 1) * 1.1;
    const plotWidth = svgWidth - padding.left - padding.right;
    const plotHeight = svgHeight - padding.top - padding.bottom;
    const stepX = rows.length > 1 ? plotWidth / (rows.length - 1) : plotWidth;

    return (
      <div className="card" style={{ gridColumn: `span ${widget.layout.w}`, height: '350px' }}>
        <span style={{ fontSize: '14px', fontWeight: 600 }}>{widget.title}</span>
        <div className="chart-container">
          <svg className="svg-chart" viewBox={`0 0 ${svgWidth} ${svgHeight}`} width="100%" height="100%">
            <defs>
              {/* Sleek area gradient */}
              {yKeys.map((_, keyIdx) => (
                <linearGradient key={keyIdx} id={`area-grad-${keyIdx}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={colorPalette[keyIdx % colorPalette.length]} stopOpacity="0.3"/>
                  <stop offset="100%" stopColor={colorPalette[keyIdx % colorPalette.length]} stopOpacity="0.0"/>
                </linearGradient>
              ))}
            </defs>

            {/* Grid lines */}
            {[0, 0.25, 0.5, 0.75, 1].map((ratio, idx) => {
              const yVal = plotHeight * (1 - ratio) + padding.top;
              const labelVal = yMax * ratio;
              return (
                <g key={idx}>
                  <line className="svg-grid-line" x1={padding.left} y1={yVal} x2={svgWidth - padding.right} y2={yVal} />
                  <text className="svg-axis-text" x={padding.left - 10} y={yVal + 4} textAnchor="end">
                    {formatChartTick(labelVal)}
                  </text>
                </g>
              );
            })}

            {/* Lines and points */}
            {yKeys.map((yKey, keyIdx) => {
              const lineColor = colorPalette[keyIdx % colorPalette.length];
              const points = rows.map((row, rowIdx) => {
                const xVal = padding.left + rowIdx * stepX;
                const val = Number(row[yKey]) || 0;
                const yVal = plotHeight - (val / yMax) * plotHeight + padding.top;
                return { x: xVal, y: yVal, val, rowVal: row[xKey] };
              });

              // Create Path strings
              const linePathD = points.reduce((acc, p, idx) => 
                acc + `${idx === 0 ? 'M' : 'L'} ${p.x} ${p.y} `, ''
              );
              
              const areaPathD = linePathD + 
                `L ${points[points.length - 1].x} ${svgHeight - padding.bottom} ` +
                `L ${points[0].x} ${svgHeight - padding.bottom} Z`;

              return (
                <g key={yKey}>
                  {/* Fill area */}
                  <path d={areaPathD} fill={`url(#area-grad-${keyIdx})`} />
                  
                  {/* Stroke path */}
                  <path d={linePathD} className="svg-line-path" stroke={lineColor} />

                  {/* Draw connection dots */}
                  {points.map((pt, pIdx) => (
                    <g key={pIdx}>
                      <circle
                        className="svg-line-dot"
                        cx={pt.x}
                        cy={pt.y}
                        r="4"
                        stroke={lineColor}
                        onClick={() => onDrillDown && onDrillDown(xKey, pt.rowVal, widget.title)}
                      >
                        <title>{`${pt.rowVal} (${yKey}): ${formatValue(pt.val)}`}</title>
                      </circle>
                      {keyIdx === 0 && (pIdx % Math.max(1, Math.floor(rows.length / 8)) === 0) && (
                        <text
                          className="svg-axis-text"
                          x={pt.x}
                          y={svgHeight - padding.bottom + 18}
                          textAnchor="middle"
                          style={{ fontSize: '9px' }}
                        >
                          {String(pt.rowVal).length > 8 ? `${String(pt.rowVal).substring(0, 6)}..` : String(pt.rowVal)}
                        </text>
                      )}
                    </g>
                  ))}
                </g>
              );
            })}
          </svg>
        </div>
      </div>
    );
  }

  // 6. DONUT / PIE CHART RENDERER
  if (widget.type === 'pie') {
    const svgSize = 260;
    const cx = svgSize / 2;
    const cy = svgSize / 2;
    const r = 85;
    const innerR = 55; // Donut width
    
    // Group small categories if rows size is high to prevent visual clutter
    const totalSum = rows.reduce((acc, row) => acc + (Number(row[yKeys[0]]) || 0), 0);
    
    let currentAngle = 0;

    return (
      <div className="card" style={{ gridColumn: `span ${widget.layout.w}`, height: '350px' }}>
        <span style={{ fontSize: '14px', fontWeight: 600 }}>{widget.title}</span>
        <div className="chart-container" style={{ display: 'flex', gap: '16px', justifyContent: 'space-around', alignItems: 'center' }}>
          
          <svg width={svgSize} height={svgSize} style={{ overflow: 'visible' }}>
            {rows.map((row, rowIdx) => {
              const val = Number(row[yKeys[0]]) || 0;
              if (val <= 0 || totalSum === 0) return null;
              
              const angleSize = (val / totalSum) * 360;
              const color = colorPalette[rowIdx % colorPalette.length];

              // Slice math
              const x1 = cx + r * Math.cos((currentAngle - 90) * Math.PI / 180);
              const y1 = cy + r * Math.sin((currentAngle - 90) * Math.PI / 180);
              const x2 = cx + r * Math.cos((currentAngle + angleSize - 90) * Math.PI / 180);
              const y2 = cy + r * Math.sin((currentAngle + angleSize - 90) * Math.PI / 180);
              
              const ix1 = cx + innerR * Math.cos((currentAngle - 90) * Math.PI / 180);
              const iy1 = cy + innerR * Math.sin((currentAngle - 90) * Math.PI / 180);
              const ix2 = cx + innerR * Math.cos((currentAngle + angleSize - 90) * Math.PI / 180);
              const iy2 = cy + innerR * Math.sin((currentAngle + angleSize - 90) * Math.PI / 180);

              const largeArcFlag = angleSize > 180 ? 1 : 0;
              
              const pathData = `
                M ${x1} ${y1}
                A ${r} ${r} 0 ${largeArcFlag} 1 ${x2} ${y2}
                L ${ix2} ${iy2}
                A ${innerR} ${innerR} 0 ${largeArcFlag} 0 ${ix1} ${iy1}
                Z
              `;

              currentAngle += angleSize;

              return (
                <path
                  key={rowIdx}
                  d={pathData}
                  fill={color}
                  stroke="var(--bg-secondary)"
                  strokeWidth="2"
                  style={{ cursor: 'pointer', transition: 'opacity 0.2s', transformBox: 'fill-box' }}
                  onClick={() => onDrillDown && onDrillDown(xKey, row[xKey], widget.title)}
                  onMouseOver={(e) => { e.currentTarget.style.opacity = '0.85'; }}
                  onMouseOut={(e) => { e.currentTarget.style.opacity = '1'; }}
                >
                  <title>{`${String(row[xKey])}: ${formatValue(val)} (${((val/totalSum)*100).toFixed(1)}%)`}</title>
                </path>
              );
            })}
            
            {/* Total count in center of donut */}
            <circle cx={cx} cy={cy} r={innerR - 2} fill="var(--bg-secondary)" />
            <text x={cx} y={cy - 4} textAnchor="middle" fill="var(--text-secondary)" style={{ fontSize: '10px', textTransform: 'uppercase', fontWeight: 600 }}>Total</text>
            <text x={cx} y={cy + 12} textAnchor="middle" fill="white" style={{ fontSize: '16px', fontWeight: 700 }}>{formatValue(totalSum)}</text>
          </svg>

          {/* Simple Legend */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '200px', overflowY: 'auto', paddingRight: '8px' }}>
            {rows.slice(0, 6).map((row, idx) => (
              <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px' }}>
                <span style={{ display: 'block', width: '10px', height: '10px', borderRadius: '2px', backgroundColor: colorPalette[idx % colorPalette.length], flexShrink: 0 }} />
                <span style={{ color: 'var(--text-secondary)', textOverflow: 'ellipsis', overflow: 'hidden', whiteSpace: 'nowrap', maxWidth: '100px' }}>
                  {String(row[xKey])}
                </span>
                <span style={{ fontWeight: 600, color: 'var(--text-primary)', marginLeft: 'auto' }}>
                  {formatValue(row[yKeys[0]])}
                </span>
              </div>
            ))}
            {rows.length > 6 && (
              <span style={{ fontSize: '10px', color: 'var(--text-muted)', fontStyle: 'italic' }}>+{rows.length - 6} more rows</span>
            )}
          </div>

        </div>
      </div>
    );
  }

  return null;
};
