import React, { useEffect, useState, useCallback } from 'react';
import { Zap, ZapOff, Loader2, RefreshCw, Activity } from 'lucide-react';
import { aiAnalyticsApi } from '../../services/aiAnalyticsApi';

/**
 * Worker control panel rendered at the bottom of the sidebar, above the
 * user profile. Lets an operator start/stop the AI Analytics Worker and
 * trigger a historical backfill without restarting the container.
 *
 * The worker's projection cache (``ai_invoice_analytics``) must be
 * populated for the AI dashboards to show data via the projection read
 * path. When the worker is off and the projection is empty, every AI
 * widget shows nothing — this panel is the single control to fix that.
 */
export const WorkerToggle: React.FC = () => {
  const [running, setRunning] = useState(false);
  const [backfillRunning, setBackfillRunning] = useState(false);
  const [loading, setLoading] = useState(false);
  const [backfillLoading, setBackfillLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [projectionCount, setProjectionCount] = useState<number | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const status = await aiAnalyticsApi.getWorkerStatus();
      setRunning(status.health.status === 'running' || status.health.status === 'reconciling');
      setProjectionCount(status.sync_integrity?.projection_count ?? null);
      // Sync backfill state with the backend — clears "Backfilling…" when done
      setBackfillRunning(status.backfill_running ?? false);
      setError(null);
    } catch {
      // Worker status endpoint may 503 if not ready — don't spam errors
      setRunning(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    // 15s aligns with the sidebar's low-urgency status display. The
    // SyncHealthIndicator uses 30s; 5s was overly aggressive for a
    // sidebar widget and produced unnecessary backend hits. The toggle
    // itself triggers an immediate fetchStatus() after start/stop, so
    // user-initiated state changes still reflect instantly.
    const interval = setInterval(fetchStatus, 15000);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  const handleToggle = async () => {
    setLoading(true);
    setError(null);
    try {
      if (running) {
        await aiAnalyticsApi.stopWorker();
        setRunning(false);
      } else {
        await aiAnalyticsApi.startWorker();
        setRunning(true);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Worker control failed';
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const handleBackfill = async () => {
    setBackfillLoading(true);
    setError(null);
    try {
      await aiAnalyticsApi.triggerBackfill();
      setBackfillRunning(true);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Backfill failed to start';
      setError(msg);
    } finally {
      setBackfillLoading(false);
    }
  };

  const statusColor = running
    ? 'var(--color-success, #22c55e)'
    : 'var(--text-muted)';

  return (
    <div
      style={{
        padding: '10px 12px',
        borderTop: '1px solid var(--border-color)',
        display: 'flex',
        flexDirection: 'column',
        gap: '8px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <button
          onClick={handleToggle}
          disabled={loading}
          style={{
            background: 'none',
            border: 'none',
            color: running ? statusColor : 'var(--text-muted)',
            cursor: loading ? 'wait' : 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            fontSize: '12px',
            fontWeight: 600,
            padding: '4px 0',
          }}
          title={running ? 'Stop AI Analytics Worker' : 'Start AI Analytics Worker'}
        >
          {loading ? (
            <Loader2 size={14} className="loader" />
          ) : running ? (
            <Zap size={14} />
          ) : (
            <ZapOff size={14} />
          )}
          <span>AI Worker</span>
        </button>
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '4px',
            fontSize: '10px',
            color: statusColor,
            marginLeft: 'auto',
            textTransform: 'uppercase',
            letterSpacing: '0.04em',
          }}
        >
          <Activity size={10} />
          {running ? 'On' : 'Off'}
        </span>
      </div>

      {projectionCount !== null && (
        <div style={{ fontSize: '10px', color: 'var(--text-muted)' }}>
          Projections: {projectionCount.toLocaleString()}
        </div>
      )}

      <button
        onClick={handleBackfill}
        disabled={backfillLoading || backfillRunning}
        style={{
          background: 'none',
          border: '1px solid var(--border-color)',
          borderRadius: 'var(--border-radius-md, 6px)',
          color: backfillRunning ? 'var(--color-success, #22c55e)' : 'var(--text-secondary)',
          cursor: backfillLoading || backfillRunning ? 'wait' : 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '6px',
          fontSize: '11px',
          fontWeight: 600,
          padding: '5px 8px',
          width: '100%',
        }}
        title="Backfill all historical AI records into the projection cache"
      >
        {backfillLoading ? (
          <Loader2 size={12} className="loader" />
        ) : backfillRunning ? (
          <RefreshCw size={12} className="loader" />
        ) : (
          <RefreshCw size={12} />
        )}
        {backfillRunning ? 'Backfilling…' : 'Backfill'}
      </button>

      {error && (
        <div style={{ fontSize: '10px', color: 'var(--color-danger, #ef4444)', lineHeight: 1.3 }}>
          {error}
        </div>
      )}
    </div>
  );
};
