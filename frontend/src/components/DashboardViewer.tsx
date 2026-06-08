import React, { useState, useEffect } from 'react';
import { Dashboard, QueryResult, api } from '../services/api';
import { FilterBar, DashboardFilters, computeDateRange } from './FilterBar';
import { WidgetCard } from './WidgetCard';
import { X, AlertCircle } from 'lucide-react';

interface DashboardViewerProps {
  dashboard: Dashboard;
}

interface DrillDownState {
  fieldName: string;
  fieldValue: unknown;
  sourceWidget: string;
}

export const DashboardViewer: React.FC<DashboardViewerProps> = ({ dashboard }) => {
  // Server date fetched from SQL Server's GETDATE() – used for all date
  // range calculations so the dashboard aligns with the database clock.
  const [serverDate, setServerDate] = useState<string | undefined>(undefined);

  const [filters, setFilters] = useState<DashboardFilters>(() => {
    // Initialise with browser dates; will be recalculated once the server
    // date arrives (see useEffect below).
    const dates = computeDateRange('week', 1);
    return {
      department_id: undefined,
      processor_id: undefined,
      range_type: 'week',
      periods_back: 1,
      ...dates,
    };
  });

  // Fetch the database server date once on mount, then recompute filters.
  useEffect(() => {
    let active = true;
    api.getServerDate()
      .then((dateStr) => {
        if (!active) return;
        setServerDate(dateStr);
        // Recompute date range using the server date
        setFilters((prev) => {
          const rt = prev.range_type || 'week';
          const pb = prev.periods_back ?? 1;
          if (rt === 'day') return prev; // manual dates, don't override
          const dates = computeDateRange(rt, pb, dateStr);
          return { ...prev, ...dates };
        });
      })
      .catch((err) => {
        console.warn('Failed to fetch server date, using browser time:', err);
      });
    return () => { active = false; };
  }, []);

  const [drillDown, setDrillDown] = useState<DrillDownState | null>(null);
  const [drillDownData, setDrillDownData] = useState<QueryResult | null>(null);
  const [drillDownLoading, setDrillDownLoading] = useState(false);
  const [drillDownError, setDrillDownError] = useState<string | null>(null);

  // Reset drilldown when switching dashboards
  useEffect(() => {
    setDrillDown(null);
    setDrillDownData(null);
    setDrillDownError(null);
  }, [dashboard.id, dashboard._id]);

  // Execute drilldown query when drilldown state changes or global filters change
  useEffect(() => {
    if (!drillDown) {
      setDrillDownData(null);
      setDrillDownError(null);
      return;
    }

    let active = true;
    setDrillDownLoading(true);
    setDrillDownError(null);

    api.getDrillDownData(drillDown.fieldName, drillDown.fieldValue, filters)
      .then((res) => {
        if (active) {
          setDrillDownData(res);
          setDrillDownLoading(false);
        }
      })
      .catch((err) => {
        if (active) {
          console.error("Drilldown SQL query execution failed:", err);
          setDrillDownError(err.message || "Failed to load drill-down transaction rows from target database.");
          setDrillDownLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [drillDown, filters]);

  const handleDrillDown = (fieldName: string, value: unknown, widgetTitle: string) => {
    setDrillDown({
      fieldName,
      fieldValue: value,
      sourceWidget: widgetTitle,
    });
  };

  const closeDrillDown = () => {
    setDrillDown(null);
    setDrillDownData(null);
    setDrillDownError(null);
  };

  const handleFilterChange = (newFilters: DashboardFilters) => {
    setFilters(newFilters);
  };

  const formatHeader = (col: string): string => {
    // Convert Snake_Case or CamelCase to clean title space
    return col
      .replace(/([A-Z])/g, ' $1')
      .replace(/_/g, ' ')
      .trim()
      .replace(/^\w/, (c) => c.toUpperCase());
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }} data-testid="dashboard-viewer">
      {/* Dynamic Filter Bar */}
      <FilterBar filters={filters} onChange={handleFilterChange} serverDate={serverDate} />

      {/* Grid of Visualization Widgets */}
      <div className="dashboard-grid">
        {dashboard.widgets.map((widget) => (
          <WidgetCard
            key={widget.id}
            widget={widget}
            filters={filters}
            onDrillDown={handleDrillDown}
          />
        ))}
      </div>

      {/* Click-to-Drill-down Claims Workflow Table */}
      {drillDown && (
        <div className="card" style={{ marginTop: '16px', animation: 'fadeIn 0.25s ease-out' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border-color)', paddingBottom: '12px' }}>
            <div>
              <h3 style={{ fontSize: '15px', fontWeight: 600, color: 'white' }}>
                Drill-down: Detailed Claims Records
              </h3>
              <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>
                Filtering by <strong>{drillDown.fieldName} = "{String(drillDown.fieldValue)}"</strong> from <em>{drillDown.sourceWidget}</em>.
              </p>
            </div>
            <button
              onClick={closeDrillDown}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--text-muted)',
                cursor: 'pointer',
                padding: '4px',
                borderRadius: '50%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'background-color 0.2s'
              }}
              title="Close drill-down"
            >
              <X size={18} />
            </button>
          </div>

          {drillDownLoading && (
            <div style={{ padding: '40px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '12px' }}>
              <div className="loader" />
              <span style={{ fontSize: '13px', color: 'var(--text-muted)' }}>Retrieving claims database records...</span>
            </div>
          )}

          {drillDownError && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '12px', color: 'var(--color-danger)', padding: '24px', textAlign: 'center' }}>
              <AlertCircle size={32} />
              <span style={{ fontSize: '14px', fontWeight: 600 }}>SQL Execution Failed</span>
              <p style={{ fontSize: '12px', fontFamily: 'monospace', color: 'var(--text-secondary)' }}>{drillDownError}</p>
            </div>
          )}

          {!drillDownLoading && !drillDownError && drillDownData && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div className="table-container" style={{ maxHeight: '400px', overflowY: 'auto' }}>
                <table className="data-table">
                  <thead>
                    <tr>
                      {drillDownData.columns.map((col) => (
                        <th key={col}>{formatHeader(col)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {drillDownData.rows.length === 0 ? (
                      <tr>
                        <td colSpan={drillDownData.columns.length} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '24px' }}>
                          No operational claims matched this filter criteria.
                        </td>
                      </tr>
                    ) : (
                      drillDownData.rows.map((row, idx) => (
                        <tr key={idx}>
                          {drillDownData.columns.map((col) => {
                            const val = row[col];
                            // Special styling for status badges inside table
                            if (col.toLowerCase() === 'status' && typeof val === 'string') {
                              return (
                                <td key={col}>
                                  <span className={`badge badge-${val.toLowerCase()}`}>
                                    {val}
                                  </span>
                                </td>
                              );
                            }
                            return <td key={col}>{val === null || val === undefined ? '-' : String(val)}</td>;
                          })}
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', textAlign: 'right' }}>
                Showing up to {drillDownData.rows.length} rows returned from target database.
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
