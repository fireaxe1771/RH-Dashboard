import React, { useState, useEffect } from 'react';
import { 
  Dashboard, 
  Widget, 
  SQLTableSchema, 
  QueryResult, 
  api 
} from '../services/api';
import { 
  Save, 
  X, 
  Plus, 
  Trash2, 
  Play, 
  Info,
  Database,
  Eye,
  Settings,
  AlertCircle
} from 'lucide-react';

interface DashboardDesignerProps {
  initialDashboard?: Dashboard | null;
  onSave: () => void;
  onCancel: () => void;
}

export const DashboardDesigner: React.FC<DashboardDesignerProps> = ({
  initialDashboard,
  onSave,
  onCancel,
}) => {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [widgets, setWidgets] = useState<Widget[]>([]);
  const [activeWidgetId, setActiveWidgetId] = useState<string | null>(null);
  
  // SQL Schema for helper sidebar
  const [schema, setSchema] = useState<SQLTableSchema[]>([]);
  const [schemaLoading, setSchemaLoading] = useState(true);
  const [schemaError, setSchemaError] = useState<string | null>(null);

  // SQL Query Tester state
  const [testLoading, setTestLoading] = useState(false);
  const [testResult, setTestResult] = useState<QueryResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  // Load initial data if editing
  useEffect(() => {
    if (initialDashboard) {
      setName(initialDashboard.name);
      setDescription(initialDashboard.description || '');
      setWidgets(JSON.parse(JSON.stringify(initialDashboard.widgets))); // Deep copy
      if (initialDashboard.widgets.length > 0) {
        setActiveWidgetId(initialDashboard.widgets[0].id);
      }
    } else {
      setName('');
      setDescription('');
      setWidgets([]);
      setActiveWidgetId(null);
    }
  }, [initialDashboard]);

  // Load target SQL schemas
  useEffect(() => {
    setSchemaLoading(true);
    setSchemaError(null);
    api.getSqlSchema()
      .then((data) => {
        setSchema(data);
        setSchemaLoading(false);
      })
      .catch((err) => {
        console.error("Failed to retrieve SQL database schema:", err);
        setSchemaError(err.message || "Failed to retrieve SQL schemas from target Azure SQL Database.");
        setSchemaLoading(false);
      });
  }, []);

  const handleAddWidget = () => {
    const newId = `widget_${Date.now()}`;
    const newWidget: Widget = {
      id: newId,
      title: 'New Widget',
      type: 'bar',
      sql_query: 'SELECT Status, COUNT(*) as Count FROM Claims GROUP BY Status',
      layout: { x: 0, y: 0, w: 6, h: 4 },
      config: {
        xAxisKey: '',
        yAxisKeys: [],
        colors: ['#6366f1']
      }
    };
    setWidgets([...widgets, newWidget]);
    setActiveWidgetId(newId);
    setTestResult(null);
    setTestError(null);
  };

  const handleDeleteWidget = (id: string) => {
    const updated = widgets.filter(w => w.id !== id);
    setWidgets(updated);
    if (activeWidgetId === id) {
      setActiveWidgetId(updated.length > 0 ? updated[0].id : null);
      setTestResult(null);
      setTestError(null);
    }
  };

  const updateActiveWidget = (updater: (w: Widget) => Widget) => {
    setWidgets(widgets.map(w => w.id === activeWidgetId ? updater(w) : w));
  };

  const activeWidget = widgets.find(w => w.id === activeWidgetId);

  // Test SQL query against target
  const handleTestQuery = () => {
    if (!activeWidget || !activeWidget.sql_query) return;
    setTestLoading(true);
    setTestResult(null);
    setTestError(null);

    api.runSqlQuery(activeWidget.sql_query, {})
      .then((res) => {
        setTestResult(res);
        setTestLoading(false);
        // Automatically pre-fill Axis keys if they are empty
        if (res.columns.length > 0) {
          const defaultX = res.columns[0];
          const defaultY = res.columns.filter(c => c !== defaultX).slice(0, 1);
          updateActiveWidget(w => ({
            ...w,
            config: {
              ...w.config,
              xAxisKey: w.config.xAxisKey || defaultX,
              yAxisKeys: w.config.yAxisKeys?.length ? w.config.yAxisKeys : defaultY
            }
          }));
        }
      })
      .catch((err) => {
        setTestError(err.message || "Failed to execute query.");
        setTestLoading(false);
      });
  };

  const handleSave = async () => {
    if (!name.trim()) {
      alert("Dashboard Name is required.");
      return;
    }
    
    const dashboardData: Dashboard = {
      name,
      description,
      widgets,
    };

    try {
      if (initialDashboard && (initialDashboard.id || initialDashboard._id)) {
        const id = initialDashboard.id || initialDashboard._id || '';
        await api.updateDashboard(id, dashboardData);
      } else {
        await api.createDashboard(dashboardData);
      }
      onSave();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unknown error saving dashboard.";
      alert(`Error saving dashboard: ${msg}`);
    }
  };

  return (
    <div className="designer-grid" data-testid="dashboard-designer">
      {/* LEFT: WORKSPACE / EDIT PANEL */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        
        {/* Core details */}
        <div className="card">
          <div style={{ display: 'flex', gap: '16px' }}>
            <div style={{ flex: 1 }}>
              <label className="input-label">Dashboard Name</label>
              <input
                type="text"
                className="input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Invoicing & Performance Runs"
              />
            </div>
            <div style={{ flex: 2 }}>
              <label className="input-label">Description (Optional)</label>
              <input
                type="text"
                className="input"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Briefly state the goal of this dashboard"
              />
            </div>
          </div>
        </div>

        {/* Workspace builder */}
        <div style={{ display: 'flex', flex: 1, gap: '20px', minHeight: '0' }}>
          
          {/* Widgets list */}
          <div className="card" style={{ width: '240px', flexShrink: 0, minHeight: '0', overflowY: 'auto' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)' }}>Widgets</span>
              <button className="btn btn-primary" onClick={handleAddWidget} style={{ padding: '6px 10px', fontSize: '12px' }}>
                <Plus size={14} />
                <span>Add</span>
              </button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '12px' }}>
              {widgets.map(w => (
                <div
                  key={w.id}
                  onClick={() => {
                    setActiveWidgetId(w.id);
                    setTestResult(null);
                    setTestError(null);
                  }}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '10px 12px',
                    borderRadius: 'var(--border-radius-md)',
                    border: '1px solid',
                    borderColor: w.id === activeWidgetId ? 'var(--accent-primary)' : 'var(--border-color)',
                    backgroundColor: w.id === activeWidgetId ? 'var(--accent-glow)' : 'rgba(0,0,0,0.1)',
                    cursor: 'pointer',
                    fontSize: '13px'
                  }}
                >
                  <span style={{ textOverflow: 'ellipsis', overflow: 'hidden', whiteSpace: 'nowrap', maxWidth: '140px' }}>
                    {w.title}
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDeleteWidget(w.id);
                    }}
                    style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer' }}
                    title="Delete widget"
                  >
                    <Trash2 size={13} onMouseOver={(e) => e.currentTarget.style.color = 'var(--color-danger)'} onMouseOut={(e) => e.currentTarget.style.color = 'var(--text-muted)'} />
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* Active Widget Editor */}
          <div className="card" style={{ flex: 1, minHeight: '0', overflowY: 'auto' }}>
            {activeWidget ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                
                {/* Title and size config */}
                <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr', gap: '12px' }}>
                  <div>
                    <label className="input-label">Widget Title</label>
                    <input
                      type="text"
                      className="input"
                      value={activeWidget.title}
                      onChange={(e) => updateActiveWidget(w => ({ ...w, title: e.target.value }))}
                    />
                  </div>
                  <div>
                    <label className="input-label">Width Columns</label>
                    <select
                      className="input"
                      value={activeWidget.layout.w}
                      onChange={(e) => updateActiveWidget(w => ({ ...w, layout: { ...w.layout, w: Number(e.target.value) } }))}
                    >
                      <option value={4}>4 (1/3 Width)</option>
                      <option value={6}>6 (1/2 Width)</option>
                      <option value={8}>8 (2/3 Width)</option>
                      <option value={12}>12 (Full Width)</option>
                    </select>
                  </div>
                  <div>
                    <label className="input-label">Display Type</label>
                    <select
                      className="input"
                      value={activeWidget.type}
                      onChange={(e) => updateActiveWidget(w => ({ ...w, type: e.target.value as Widget['type'] }))}
                    >
                      <option value="stat">Stat Metric</option>
                      <option value="bar">Bar Chart</option>
                      <option value="line">Line Chart</option>
                      <option value="pie">Donut Chart</option>
                      <option value="table">Data Table</option>
                      <option value="looker">Looker Studio Embed</option>
                    </select>
                  </div>
                </div>

                {/* Query or URL settings */}
                {activeWidget.type === 'looker' ? (
                  <div>
                    <label className="input-label">Looker Studio Embed URL</label>
                    <input
                      type="text"
                      className="input"
                      value={activeWidget.config.embedUrl || ''}
                      onChange={(e) => updateActiveWidget(w => ({ ...w, config: { ...w.config, embedUrl: e.target.value } }))}
                      placeholder="https://lookerstudio.google.com/embed/reporting/..."
                    />
                    <div style={{ display: 'flex', gap: '8px', marginTop: '8px', color: 'var(--text-muted)', fontSize: '11px', lineHeight: '1.4' }}>
                      <Info size={14} style={{ flexShrink: 0 }} />
                      <span>Make sure the Looker Studio report has embedding enabled and permissions are configured correctly.</span>
                    </div>
                  </div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                    {/* SQL Input Area */}
                    <div className="sql-editor-container">
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <label className="input-label">SQL Query (Target Azure SQL)</label>
                        <button
                          className="btn btn-primary"
                          onClick={handleTestQuery}
                          disabled={testLoading}
                          style={{ padding: '6px 12px', fontSize: '12px', display: 'flex', alignItems: 'center', gap: '4px' }}
                        >
                          <Play size={12} />
                          <span>Test & Fetch Columns</span>
                        </button>
                      </div>
                      <textarea
                        className="sql-textarea"
                        value={activeWidget.sql_query || ''}
                        onChange={(e) => updateActiveWidget(w => ({ ...w, sql_query: e.target.value }))}
                        placeholder="SELECT Status, COUNT(*) FROM Claims GROUP BY Status"
                      />
                    </div>

                    {/* SQL Test feedback */}
                    {testError && (
                      <div style={{ display: 'flex', gap: '8px', padding: '12px', backgroundColor: 'rgba(239, 68, 68, 0.1)', borderRadius: 'var(--border-radius-md)', border: '1px solid rgba(239, 68, 68, 0.2)', color: 'var(--color-danger)', fontSize: '12px', fontFamily: 'monospace' }}>
                        <AlertCircle size={16} style={{ flexShrink: 0 }} />
                        <span>{testError}</span>
                      </div>
                    )}

                    {/* Chart Keys Selection */}
                    {testResult && (
                      <div className="card" style={{ padding: '16px', backgroundColor: 'rgba(0,0,0,0.15)', border: '1px solid var(--border-color)' }}>
                        <span style={{ fontSize: '12px', fontWeight: 600, color: 'white', display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <Eye size={12} />
                          Query Executed (First 3 Rows Returned)
                        </span>
                        
                        {/* Sample Rows table */}
                        <div style={{ overflowX: 'auto', marginTop: '10px' }}>
                          <table className="data-table" style={{ fontSize: '11px' }}>
                            <thead>
                              <tr>
                                {testResult.columns.map(c => <th key={c} style={{ padding: '6px' }}>{c}</th>)}
                              </tr>
                            </thead>
                            <tbody>
                              {testResult.rows.slice(0, 3).map((r, ri) => (
                                <tr key={ri}>
                                  {testResult.columns.map(c => <td key={c} style={{ padding: '6px' }}>{String(r[c])}</td>)}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>

                        {/* Dropdown selectors for mapping */}
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '12px', marginTop: '16px' }}>
                          <div>
                            <label className="input-label" style={{ fontSize: '11px' }}>Label Column (X-Axis)</label>
                            <select
                              className="input"
                              style={{ padding: '6px', fontSize: '12px' }}
                              value={activeWidget.config.xAxisKey || ''}
                              onChange={(e) => updateActiveWidget(w => ({ ...w, config: { ...w.config, xAxisKey: e.target.value } }))}
                            >
                              <option value="">Select Column</option>
                              {testResult.columns.map(c => <option key={c} value={c}>{c}</option>)}
                            </select>
                          </div>
                          <div>
                            <label className="input-label" style={{ fontSize: '11px' }}>Metric Column (Y-Axis)</label>
                            <select
                              className="input"
                              style={{ padding: '6px', fontSize: '12px' }}
                              value={activeWidget.config.yAxisKeys?.[0] || ''}
                              onChange={(e) => updateActiveWidget(w => ({ ...w, config: { ...w.config, yAxisKeys: [e.target.value] } }))}
                            >
                              <option value="">Select Column</option>
                              {testResult.columns.map(c => <option key={c} value={c}>{c}</option>)}
                            </select>
                          </div>
                          <div>
                            <label className="input-label" style={{ fontSize: '11px' }}>Value Format</label>
                            <select
                              className="input"
                              style={{ padding: '6px', fontSize: '12px' }}
                              value={activeWidget.config.format || ''}
                              onChange={(e) => updateActiveWidget(w => ({ ...w, config: { ...w.config, format: e.target.value } }))}
                            >
                              <option value="">Standard (None)</option>
                              <option value="currency">Currency ($)</option>
                              <option value="percentage">Percentage (%)</option>
                            </select>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <div style={{ display: 'flex', flex: 1, alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-muted)', fontSize: '14px', flexDirection: 'column', gap: '8px' }}>
                <Settings size={28} />
                <span>Add or select a widget to begin configuring layouts & SQL.</span>
              </div>
            )}
          </div>

        </div>

        {/* Designer Footer Actions */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px' }}>
          <button className="btn" onClick={onCancel}>
            <X size={16} />
            <span>Cancel</span>
          </button>
          <button className="btn btn-primary" onClick={handleSave}>
            <Save size={16} />
            <span>Save Dashboard</span>
          </button>
        </div>

      </div>

      {/* RIGHT: DATABASE SCHEMA VIEW BAR */}
      <div className="designer-sidebar" style={{ minHeight: '0' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'white', fontWeight: 600, borderBottom: '1px solid var(--border-color)', paddingBottom: '12px', flexShrink: 0 }}>
          <Database size={16} className="trend-up" />
          <span>Azure SQL target schema</span>
        </div>

        {schemaLoading ? (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '10px' }}>
            <div className="loader" />
            <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Retrieving DB schema...</span>
          </div>
        ) : schemaError ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px', color: 'var(--color-danger)', padding: '12px', textAlign: 'center', fontSize: '12px' }}>
            <AlertCircle size={24} />
            <span>Schema Unreachable</span>
            <p style={{ fontSize: '10px', color: 'var(--text-secondary)' }}>{schemaError}</p>
          </div>
        ) : (
          <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '16px', paddingRight: '4px' }}>
            {schema.map(tbl => (
              <div key={tbl.table} style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                <span style={{ fontSize: '13px', fontWeight: 600, color: '#f8fafc', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span style={{ display: 'block', width: '6px', height: '6px', borderRadius: '50%', backgroundColor: 'var(--accent-primary)' }} />
                  {tbl.table}
                </span>
                <div style={{ paddingLeft: '12px', display: 'flex', flexDirection: 'column', gap: '4px', borderLeft: '1px solid var(--border-color)' }}>
                  {tbl.columns.map(col => (
                    <div key={col.name} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px' }}>
                      <span style={{ color: 'var(--text-secondary)', fontFamily: 'monospace' }}>{col.name}</span>
                      <span style={{ color: 'var(--text-muted)' }}>{col.type.toLowerCase()}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
