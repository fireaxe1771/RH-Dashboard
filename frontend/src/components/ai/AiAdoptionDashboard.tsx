import React, { useEffect, useMemo, useState } from 'react';
import {
  BarChart3,
  Users,
  Download,
  Calendar,
  Loader2,
  CheckCircle2,
  XCircle,
  HelpCircle,
} from 'lucide-react';
import { aiAdoptionApi, AiAdoptionResponse } from '../../services/aiAdoptionApi';
import { exportToCsv, exportToExcel } from '../../utils/export';
import { billingStyles, LoadingState, ErrorState } from '../billing/shared';

const fmt = (d: Date): string => d.toISOString().split('T')[0];

const statusLabels: Record<string, string> = {
  all: 'All Departments',
  using_ai: 'Using AI',
  not_using_ai: 'Not Using AI',
  unknown: 'Unknown',
};

const badgeStyle = (status: string): React.CSSProperties => {
  switch (status) {
    case 'using_ai':
      return { backgroundColor: 'rgba(34, 197, 94, 0.15)', color: '#22c55e' };
    case 'not_using_ai':
      return { backgroundColor: 'rgba(239, 68, 68, 0.15)', color: '#ef4444' };
    case 'unknown':
    default:
      return { backgroundColor: 'rgba(148, 163, 184, 0.15)', color: '#94a3b8' };
  }
};

const kpiCardStyle: React.CSSProperties = {
  ...billingStyles.card,
  display: 'flex',
  flexDirection: 'column',
  gap: '8px',
  minHeight: '100px',
};

export const AiAdoptionDashboard: React.FC = () => {
  const today = useMemo(() => fmt(new Date()), []);
  const weekAgo = useMemo(() => {
    const d = new Date();
    d.setDate(d.getDate() - 7);
    return fmt(d);
  }, []);

  const [startDate, setStartDate] = useState(weekAgo);
  const [endDate, setEndDate] = useState(today);
  const [limit, setLimit] = useState(50);
  const [aiStatus, setAiStatus] = useState<string>('all');
  const [data, setData] = useState<AiAdoptionResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    aiAdoptionApi
      .getDepartments(startDate, endDate, limit, aiStatus)
      .then((res) => {
        if (active) {
          setData(res);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (active) {
          setError(err.message || 'Failed to load AI adoption data.');
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [startDate, endDate, limit, aiStatus]);

  const columns = useMemo(
    () => [
      'Rank',
      'Department ID',
      'Department Name',
      'State',
      'Submitted Drafts',
      '% of Volume',
      'AI Status',
      'AI Mode',
    ],
    []
  );

  const rows = useMemo(() => {
    if (!data) return [];
    return data.departments.map((d) => ({
      Rank: d.rank_overall,
      'Department ID': d.department_id,
      'Department Name': d.department_name || '—',
      State: d.state || '—',
      'Submitted Drafts': d.submitted_drafts,
      '% of Volume': `${d.percent_of_total_volume.toFixed(2)}%`,
      'AI Status': statusLabels[d.ai_status] || d.ai_status,
      'AI Mode': d.ai_mode,
    }));
  }, [data]);

  const handleExportCsv = () => {
    exportToCsv('AI_Adoption_Departments', columns, rows);
  };

  const handleExportExcel = () => {
    exportToExcel('AI_Adoption_Departments', columns, rows);
  };

  const tabs: string[] = ['all', 'using_ai', 'not_using_ai', 'unknown'];

  if (loading && !data) return <LoadingState label="Loading AI adoption data…" />;
  if (error) return <ErrorState message={error} />;
  if (!data) return null;

  const { summary } = data;

  return (
    <div style={billingStyles.page}>
      <div style={billingStyles.card}>
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: '16px',
            alignItems: 'flex-end',
          }}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
              <Calendar size={10} /> Start Date
            </span>
            <input
              type="date"
              className="input"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              style={{ padding: '7px 12px', fontSize: '13px' }}
            />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
              <Calendar size={10} /> End Date
            </span>
            <input
              type="date"
              className="input"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              style={{ padding: '7px 12px', fontSize: '13px' }}
            />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
              Top N
            </span>
            <select
              className="input"
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              style={{ padding: '8px 12px', fontSize: '13px' }}
            >
              <option value={25}>25</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
            </select>
          </div>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: '10px' }}>
            <button className="btn" onClick={handleExportCsv} style={{ fontSize: '13px' }}>
              <Download size={14} /> CSV
            </button>
            <button className="btn" onClick={handleExportExcel} style={{ fontSize: '13px' }}>
              <Download size={14} /> Excel
            </button>
          </div>
        </div>
        <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '12px' }}>
          <HelpCircle size={12} style={{ verticalAlign: 'text-bottom' }} />{' '}
          {data.ai_status_basis === 'current_configuration'
            ? 'AI Status reflects current department configuration. Submission counts reflect the selected period.'
            : data.ai_status_basis}
        </p>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
          gap: '16px',
        }}
      >
        <div style={kpiCardStyle}>
          <span style={{ fontSize: '13px', color: 'var(--text-secondary)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '6px' }}>
            <Users size={14} style={{ color: 'var(--accent-primary)' }} /> Active Departments
          </span>
          <span style={{ fontSize: '26px', fontWeight: 700, color: 'var(--text-primary)' }}>{summary.active_departments}</span>
        </div>
        <div style={kpiCardStyle}>
          <span style={{ fontSize: '13px', color: 'var(--text-secondary)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '6px' }}>
            <CheckCircle2 size={14} style={{ color: '#22c55e' }} /> Using AI
          </span>
          <span style={{ fontSize: '26px', fontWeight: 700, color: 'var(--text-primary)' }}>{summary.departments_using_ai}</span>
        </div>
        <div style={kpiCardStyle}>
          <span style={{ fontSize: '13px', color: 'var(--text-secondary)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '6px' }}>
            <XCircle size={14} style={{ color: '#ef4444' }} /> Not Using AI
          </span>
          <span style={{ fontSize: '26px', fontWeight: 700, color: 'var(--text-primary)' }}>{summary.departments_not_using_ai}</span>
        </div>
        <div style={kpiCardStyle}>
          <span style={{ fontSize: '13px', color: 'var(--text-secondary)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '6px' }}>
            <BarChart3 size={14} style={{ color: 'var(--color-info)' }} /> AI Coverage
          </span>
          <span style={{ fontSize: '26px', fontWeight: 700, color: 'var(--text-primary)' }}>{summary.ai_coverage_percent.toFixed(1)}%</span>
        </div>
      </div>

      <div
        style={{
          display: 'flex',
          gap: '8px',
          borderBottom: '1px solid var(--border-color)',
        }}
      >
        {tabs.map((tab) => (
          <button
            key={tab}
            onClick={() => setAiStatus(tab)}
            style={{
              padding: '10px 16px',
              fontSize: '13px',
              fontWeight: 600,
              background: aiStatus === tab ? 'var(--bg-secondary)' : 'transparent',
              border: 'none',
              borderBottom: aiStatus === tab ? '2px solid var(--accent-primary)' : '2px solid transparent',
              color: aiStatus === tab ? 'var(--text-primary)' : 'var(--text-muted)',
              cursor: 'pointer',
            }}
          >
            {statusLabels[tab]}
          </button>
        ))}
      </div>

      <div style={billingStyles.card}>
        {loading && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '24px', color: 'var(--text-secondary)' }}>
            <Loader2 size={18} className="loader" />
            <span>Refreshing…</span>
          </div>
        )}
        {!loading && data.departments.length === 0 && (
          <div style={{ padding: '32px', textAlign: 'center', color: 'var(--text-muted)' }}>
            No departments match the selected filters.
          </div>
        )}
        {!loading && data.departments.length > 0 && (
          <div className="table-container" style={{ overflowX: 'auto' }}>
            <table className="data-table" style={{ width: '100%' }}>
              <thead>
                <tr>
                  {columns.map((col) => (
                    <th key={col}>{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.departments.map((dept) => (
                  <tr key={dept.department_id}>
                    <td>{dept.rank_overall}</td>
                    <td>{dept.department_id}</td>
                    <td>{dept.department_name || '—'}</td>
                    <td>{dept.state || '—'}</td>
                    <td>{dept.submitted_drafts.toLocaleString()}</td>
                    <td>{dept.percent_of_total_volume.toFixed(2)}%</td>
                    <td>
                      <span
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '4px',
                          padding: '3px 8px',
                          borderRadius: '999px',
                          fontSize: '12px',
                          fontWeight: 600,
                          ...badgeStyle(dept.ai_status),
                        }}
                      >
                        {dept.ai_status === 'using_ai' && <CheckCircle2 size={12} />}
                        {dept.ai_status === 'not_using_ai' && <XCircle size={12} />}
                        {dept.ai_status === 'unknown' && <HelpCircle size={12} />}
                        {statusLabels[dept.ai_status] || dept.ai_status}
                      </span>
                    </td>
                    <td>{dept.ai_mode}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
