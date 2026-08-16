import React, { useEffect, useMemo, useState } from 'react';
import {
  BarChart3,
  Users,
  Download,
  Loader2,
  CheckCircle2,
  XCircle,
  HelpCircle,
} from 'lucide-react';
import { aiAdoptionApi, AiAdoptionResponse } from '../../services/aiAdoptionApi';
import { api } from '../../services/api';
import { exportToCsv, exportToExcel } from '../../utils/export';
import { billingStyles, LoadingState, ErrorState } from '../billing/shared';
import { computeDateRange } from '../FilterBar';
import { AiAnalyticsFilterBar } from './AiAnalyticsFilterBar';

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
  const [serverDate, setServerDate] = useState<string | undefined>(undefined);
  const [dateReady, setDateReady] = useState(false);
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [limit, setLimit] = useState(50);
  const [aiStatus, setAiStatus] = useState<string>('all');
  const [data, setData] = useState<AiAdoptionResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch the database server date once on mount, then compute the initial
  // date range (current month) so queries align with SQL Server's GETDATE().
  // Defaults to 'month' rather than 'week' because "current week" on the
  // first day of the week (Sunday) is a single-day window that typically
  // has no data yet.
  useEffect(() => {
    let active = true;
    api.getServerDate()
      .then((dateStr) => {
        if (!active) return;
        setServerDate(dateStr);
        const dates = computeDateRange('month', 0, dateStr);
        setStartDate(dates.start_date);
        setEndDate(dates.end_date);
        setDateReady(true);
      })
      .catch(() => {
        if (!active) return;
        const dates = computeDateRange('month', 0);
        setStartDate(dates.start_date);
        setEndDate(dates.end_date);
        setDateReady(true);
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!dateReady) return;
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
  }, [startDate, endDate, limit, aiStatus, dateReady]);

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

  const tabs: string[] = ['all', 'using_ai', 'not_using_ai'];

  if (!dateReady || (loading && !data)) return <LoadingState label="Loading AI adoption data…" />;
  if (error) return <ErrorState message={error} />;
  if (!data) return null;

  const { summary } = data;

  return (
    <div style={billingStyles.page}>
      <AiAnalyticsFilterBar
        startDate={startDate}
        endDate={endDate}
        onStartDateChange={setStartDate}
        onEndDateChange={setEndDate}
        serverDate={serverDate}
        defaultRangeType="month"
        defaultPeriodsBack={0}
      />

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
