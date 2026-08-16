import React, { useEffect, useState, useCallback } from 'react';
import {
  CheckCircle2,
  RefreshCw,
  AlertTriangle,
  XCircle,
  Pause,
  Loader2,
  ChevronDown,
  ChevronUp,
  Database,
  Zap,
} from 'lucide-react';
import {
  aiAnalyticsApi,
  AiSyncHealth,
  SyncStatus,
  AiDeadLetter,
} from '../../services/aiAnalyticsApi';
import { billingStyles } from '../billing/shared';

// ---------------------------------------------------------------------------
// Status configuration
// ---------------------------------------------------------------------------

interface StatusConfig {
  icon: React.ReactNode;
  label: string;
  color: string;
  bgColor: string;
  description: string;
}

const STATUS_CONFIG: Record<SyncStatus, StatusConfig> = {
  synced: {
    icon: <CheckCircle2 size={16} />,
    label: 'In Sync',
    color: '#16a34a',
    bgColor: '#dcfce7',
    description: 'Projection cache matches MongoDB. All data is current.',
  },
  syncing: {
    icon: <RefreshCw size={16} className="animate-spin" />,
    label: 'Syncing',
    color: '#2563eb',
    bgColor: '#dbeafe',
    description: 'Worker is actively processing claims and updating the cache.',
  },
  'catching-up': {
    icon: <Loader2 size={16} className="animate-spin" />,
    label: 'Catching Up',
    color: '#ca8a04',
    bgColor: '#fef9c3',
    description: 'Divergent claims detected and being resynced automatically.',
  },
  'divergence-detected': {
    icon: <AlertTriangle size={16} />,
    label: 'Divergence Detected',
    color: '#ea580c',
    bgColor: '#ffedd5',
    description: 'Cache count differs from source. Sample verification pending.',
  },
  error: {
    icon: <XCircle size={16} />,
    label: 'Sync Error',
    color: '#dc2626',
    bgColor: '#fee2e2',
    description: 'Worker or integrity check has encountered an error.',
  },
  stopped: {
    icon: <Pause size={16} />,
    label: 'Sync Stopped',
    color: '#6b7280',
    bgColor: '#f3f4f6',
    description: 'Worker is not running. Cache is not being updated.',
  },
};

// ---------------------------------------------------------------------------
// SyncHealthIndicator — compact badge with expandable detail panel
// ---------------------------------------------------------------------------

export const SyncHealthIndicator: React.FC = () => {
  const [health, setHealth] = useState<AiSyncHealth | null>(null);
  const [deadLetters, setDeadLetters] = useState<AiDeadLetter[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [resolveError, setResolveError] = useState<string | null>(null);

  const fetchHealth = useCallback(async () => {
    try {
      const [healthData, dlData] = await Promise.all([
        aiAnalyticsApi.getSyncHealth(),
        aiAnalyticsApi.getDeadLetters(50),
      ]);
      setHealth(healthData);
      setDeadLetters(dlData);
      setError(null);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load sync health';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchHealth();
    const interval = setInterval(() => void fetchHealth(), 30000);
    return () => clearInterval(interval);
  }, [fetchHealth]);

  const handleResolve = useCallback(async (claimId: number) => {
    setResolveError(null);
    try {
      await aiAnalyticsApi.resolveDeadLetter(claimId);
      void fetchHealth();
    } catch (err) {
      // The 30s health polling only refreshes the dead-letter list — it
      // doesn't tell the user their resolve attempt failed. Surface the
      // error inline so they can retry or investigate.
      const msg = err instanceof Error ? err.message : 'Failed to resolve dead-letter';
      setResolveError(`Claim #${claimId}: ${msg}`);
    }
  }, [fetchHealth]);

  if (loading && !health) {
    return (
      <div style={{ ...billingStyles.card, padding: '8px 12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
        <Loader2 size={16} className="animate-spin" style={{ color: '#6b7280' }} />
        <span style={{ fontSize: '13px', color: '#6b7280' }}>Checking sync status…</span>
      </div>
    );
  }

  if (error && !health) {
    return (
      <div style={{ ...billingStyles.card, padding: '8px 12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
        <AlertTriangle size={16} style={{ color: '#dc2626' }} />
        <span style={{ fontSize: '13px', color: '#dc2626' }}>Sync status unavailable</span>
      </div>
    );
  }

  if (!health) return null;

  const config = STATUS_CONFIG[health.status] || STATUS_CONFIG.stopped;

  return (
    <div style={{ ...billingStyles.card, padding: '0', minWidth: '280px' }}>
      {/* Compact badge row */}
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '8px 12px',
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          width: '100%',
          textAlign: 'left',
        }}
      >
        <span
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '4px 10px',
            borderRadius: '12px',
            backgroundColor: config.bgColor,
            color: config.color,
            fontSize: '13px',
            fontWeight: 600,
            whiteSpace: 'nowrap',
          }}
        >
          {config.icon}
          {config.label}
        </span>
        {deadLetters.length > 0 && (
          <span
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              padding: '2px 8px',
              borderRadius: '10px',
              backgroundColor: '#fee2e2',
              color: '#dc2626',
              fontSize: '12px',
              fontWeight: 600,
            }}
          >
            <AlertTriangle size={12} />
            {deadLetters.length} dead-letter{deadLetters.length !== 1 ? 's' : ''}
          </span>
        )}
        <span style={{ marginLeft: 'auto' }}>
          {expanded ? <ChevronUp size={14} color="#6b7280" /> : <ChevronDown size={14} color="#6b7280" />}
        </span>
      </button>

      {/* Expandable detail panel */}
      {expanded && (
        <div
          style={{
            borderTop: '1px solid #e5e7eb',
            padding: '12px',
            display: 'flex',
            flexDirection: 'column',
            gap: '12px',
          }}
        >
          {/* Status description */}
          <div style={{ fontSize: '12px', color: '#6b7280' }}>
            {config.description}
          </div>

          {/* Sync integrity stats */}
          <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
            <SyncStat
              icon={<Database size={14} color="#6b7280" />}
              label="Source"
              value={health.sync_integrity.source_count}
            />
            <SyncStat
              icon={<Database size={14} color="#6b7280" />}
              label="Cache"
              value={health.sync_integrity.projection_count}
            />
            {health.sync_integrity.divergent_count > 0 && (
              <SyncStat
                icon={<AlertTriangle size={14} color="#ea580c" />}
                label="Divergent"
                value={health.sync_integrity.divergent_count}
                color="#ea580c"
              />
            )}
            {health.sync_integrity.missing_count > 0 && (
              <SyncStat
                icon={<XCircle size={14} color="#dc2626" />}
                label="Missing"
                value={health.sync_integrity.missing_count}
                color="#dc2626"
              />
            )}
          </div>

          {/* Last check time */}
          {health.sync_integrity.last_check_at && (
            <div style={{ fontSize: '11px', color: '#9ca3af' }}>
              Last integrity check: {formatTimestamp(health.sync_integrity.last_check_at)}
            </div>
          )}

          {/* Throughput metrics */}
          <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
            <SyncStat
              icon={<Zap size={14} color="#6b7280" />}
              label="Events"
              value={health.metrics.events_received}
            />
            <SyncStat
              icon={<RefreshCw size={14} color="#6b7280" />}
              label="Refreshed"
              value={health.metrics.claims_refreshed}
            />
            <SyncStat
              icon={<CheckCircle2 size={14} color="#16a34a" />}
              label="Created"
              value={health.metrics.projections_created}
            />
          </div>

          {/* Error display */}
          {health.last_error && (
            <div
              style={{
                padding: '8px 10px',
                backgroundColor: '#fee2e2',
                borderRadius: '6px',
                fontSize: '12px',
                color: '#dc2626',
              }}
            >
              <strong>Error:</strong> {health.last_error}
            </div>
          )}

          {/* Dead-letter list */}
          {deadLetters.length > 0 && (
            <DeadLetterList deadLetters={deadLetters} onResolve={handleResolve} />
          )}

          {/* Resolve attempt error — surfaced inline because the 30s
              health poll only refreshes the dead-letter list, not the
              resolve attempt outcome. */}
          {resolveError && (
            <div
              style={{
                padding: '8px 10px',
                backgroundColor: '#fee2e2',
                borderRadius: '6px',
                fontSize: '12px',
                color: '#dc2626',
              }}
            >
              <strong>Resolve failed:</strong> {resolveError}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// SyncStat — small stat display
// ---------------------------------------------------------------------------

const SyncStat: React.FC<{
  icon: React.ReactNode;
  label: string;
  value: number;
  color?: string;
}> = ({ icon, label, value, color = '#374151' }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
    {icon}
    <span style={{ fontSize: '12px', color: '#6b7280' }}>{label}:</span>
    <span style={{ fontSize: '12px', fontWeight: 600, color }}>{value.toLocaleString()}</span>
  </div>
);

// ---------------------------------------------------------------------------
// DeadLetterList — inline list of dead-lettered claims
// ---------------------------------------------------------------------------

const DeadLetterList: React.FC<{
  deadLetters: AiDeadLetter[];
  onResolve: (claimId: number) => void;
}> = ({ deadLetters, onResolve }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
    <div style={{ fontSize: '12px', fontWeight: 600, color: '#dc2626' }}>
      Dead-Lettered Claims ({deadLetters.length})
    </div>
    {deadLetters.slice(0, 10).map((dl) => (
      <div
        key={dl._id}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '6px 8px',
          backgroundColor: '#fff',
          border: '1px solid #e5e7eb',
          borderRadius: '6px',
          fontSize: '12px',
        }}
      >
        <span style={{ fontWeight: 600, minWidth: '60px' }}>
          #{dl.claim_id ?? 'unknown'}
        </span>
        <span style={{ color: '#6b7280', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {dl.error_type}: {dl.error_message}
        </span>
        <span style={{ color: '#9ca3af', fontSize: '11px' }}>
          {dl.attempt_count} attempt{dl.attempt_count !== 1 ? 's' : ''}
        </span>
        {dl.claim_id !== null && (
          <button
            onClick={() => onResolve(dl.claim_id as number)}
            style={{
              padding: '2px 8px',
              fontSize: '11px',
              border: '1px solid #d1d5db',
              borderRadius: '4px',
              background: 'none',
              cursor: 'pointer',
              color: '#374151',
            }}
          >
            Resolve
          </button>
        )}
      </div>
    ))}
    {deadLetters.length > 10 && (
      <div style={{ fontSize: '11px', color: '#9ca3af' }}>
        + {deadLetters.length - 10} more…
      </div>
    )}
  </div>
);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTimestamp(iso: string): string {
  try {
    const dt = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - dt.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    return dt.toLocaleDateString();
  } catch {
    return iso;
  }
}
