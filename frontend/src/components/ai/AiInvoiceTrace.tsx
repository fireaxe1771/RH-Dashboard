import React, { useEffect, useState } from 'react';
import { ArrowLeft, FileText, MessageSquare, GitCompare, Clock } from 'lucide-react';
import {
  aiAnalyticsApi,
  AiInvoiceTrace as AiInvoiceTraceType,
  AiConversationRecord,
  AiLineItemEntry,
  AiFinalLineItemEntry,
  AiLineItemComparison,
} from '../../services/aiAnalyticsApi';
import { billingStyles, LoadingState, ErrorState, EmptyState, formatCurrency } from '../billing/shared';

interface Props {
  claimId: number;
  onBack: () => void;
}

const sectionTitleStyle: React.CSSProperties = {
  fontSize: '15px',
  fontWeight: 700,
  color: 'var(--text-primary)',
  margin: '0 0 16px 0',
  display: 'flex',
  alignItems: 'center',
  gap: '8px',
};

const labelStyle: React.CSSProperties = {
  fontSize: '11px',
  fontWeight: 600,
  color: 'var(--text-muted)',
  textTransform: 'uppercase',
};

const valueStyle: React.CSSProperties = {
  fontSize: '14px',
  color: 'var(--text-primary)',
};

const outcomeBadgeStyle = (outcome: string): React.CSSProperties => {
  switch (outcome) {
    case 'released': return { backgroundColor: 'rgba(34, 197, 94, 0.15)', color: '#22c55e', padding: '4px 12px', borderRadius: '4px', fontSize: '12px', fontWeight: 600 };
    case 'cancelled_rejected': return { backgroundColor: 'rgba(239, 68, 68, 0.15)', color: '#ef4444', padding: '4px 12px', borderRadius: '4px', fontSize: '12px', fontWeight: 600 };
    case 'pending': return { backgroundColor: 'rgba(234, 179, 8, 0.15)', color: '#eab308', padding: '4px 12px', borderRadius: '4px', fontSize: '12px', fontWeight: 600 };
    default: return { backgroundColor: 'rgba(148, 163, 184, 0.15)', color: '#94a3b8', padding: '4px 12px', borderRadius: '4px', fontSize: '12px', fontWeight: 600 };
  }
};

// ---------------------------------------------------------------------------
// Timeline
// ---------------------------------------------------------------------------

const AiInvoiceTimeline: React.FC<{ trace: AiInvoiceTraceType }> = ({ trace }) => {
  const events: { time: string | null; label: string; detail?: string }[] = [];

  if (trace.claim_created_at) {
    events.push({ time: trace.claim_created_at, label: 'Claim Created', detail: `Run #${trace.run_number || '—'}` });
  }
  if (trace.inserted_at) {
    events.push({ time: trace.inserted_at, label: 'AI Processing Started', detail: trace.claim_processing_status || undefined });
  }
  if (trace.completed_at) {
    events.push({ time: trace.completed_at, label: 'AI Processing Completed', detail: trace.agent_exec_status || undefined });
  }
  for (const log of trace.process_logs) {
    events.push({ time: log.created_date, label: log.log_text || 'Log Entry', detail: `User ${log.user_id} (type ${log.user_type_id})` });
  }
  if (trace.cancellation_date) {
    events.push({ time: trace.cancellation_date, label: 'Invoice Cancelled', detail: trace.cancellation_reason || undefined });
  }
  if (trace.business_status_updated_at && trace.business_outcome === 'released') {
    events.push({ time: trace.business_status_updated_at, label: 'Invoice Released' });
  }

  // Sort by time
  events.sort((a, b) => {
    if (!a.time) return 1;
    if (!b.time) return -1;
    return a.time.localeCompare(b.time);
  });

  if (events.length === 0) return <EmptyState label="No timeline events available." />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
      {events.map((event, idx) => (
        <div key={idx} style={{ display: 'flex', gap: '12px', alignItems: 'flex-start' }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            <div style={{ width: '10px', height: '10px', borderRadius: '50%', backgroundColor: 'var(--accent-primary)', marginTop: '4px' }} />
            {idx < events.length - 1 && <div style={{ width: '2px', flex: 1, backgroundColor: 'var(--border-color)', minHeight: '20px' }} />}
          </div>
          <div style={{ flex: 1, paddingBottom: '8px' }}>
            <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>{event.label}</div>
            {event.detail && <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{event.detail}</div>}
            {event.time && (
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>
                {new Date(event.time).toLocaleString()}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Conversation viewer
// ---------------------------------------------------------------------------

const AiConversationViewer: React.FC<{ conversations: AiConversationRecord[] }> = ({ conversations }) => {
  const [expanded, setExpanded] = useState<number | null>(0);

  if (conversations.length === 0) return <EmptyState label="No agent conversations found." />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      {conversations.map((conv, idx) => (
        <div key={idx} style={{ border: '1px solid var(--border-color)', borderRadius: 'var(--border-radius-md)', overflow: 'hidden' }}>
          <button
            onClick={() => setExpanded(expanded === idx ? null : idx)}
            style={{
              width: '100%', padding: '12px 16px', display: 'flex', justifyContent: 'space-between',
              alignItems: 'center', backgroundColor: 'var(--bg-tertiary)', border: 'none', cursor: 'pointer',
            }}
          >
            <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
              <span style={{ fontSize: '13px', fontWeight: 600 }}>{conv.agent}</span>
              <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{conv.status}</span>
              <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{conv.processing_stage}</span>
            </div>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
              {conv.created_at ? new Date(conv.created_at).toLocaleString() : '—'}
            </span>
          </button>
          {expanded === idx && (
            <div style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
              {conv.input_data && (
                <div>
                  <span style={labelStyle}>Input Data</span>
                  <pre style={{ fontSize: '11px', overflow: 'auto', maxHeight: '200px', backgroundColor: 'var(--bg-primary)', padding: '8px', borderRadius: '4px' }}>
                    {JSON.stringify(conv.input_data, null, 2)}
                  </pre>
                </div>
              )}
              {conv.results && (
                <div>
                  <span style={labelStyle}>Results</span>
                  <pre style={{ fontSize: '11px', overflow: 'auto', maxHeight: '200px', backgroundColor: 'var(--bg-primary)', padding: '8px', borderRadius: '4px' }}>
                    {JSON.stringify(conv.results, null, 2)}
                  </pre>
                </div>
              )}
              {conv.output_data && Object.keys(conv.output_data).length > 0 && (
                <div>
                  <span style={labelStyle}>Output Data</span>
                  <pre style={{ fontSize: '11px', overflow: 'auto', maxHeight: '200px', backgroundColor: 'var(--bg-primary)', padding: '8px', borderRadius: '4px' }}>
                    {JSON.stringify(conv.output_data, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Line item comparison
// ---------------------------------------------------------------------------

const AiLineItemComparisonView: React.FC<{
  aiItems: AiLineItemEntry[];
  finalItems: AiFinalLineItemEntry[];
  comparison: AiLineItemComparison | null;
}> = ({ aiItems, finalItems, comparison }) => {
  if (aiItems.length === 0 && finalItems.length === 0) {
    return <EmptyState label="No line items available for comparison." />;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      {comparison && (comparison.ai_original_amount !== null || comparison.final_rh_amount !== null) && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px' }}>
          <div style={{ padding: '12px', backgroundColor: 'var(--bg-tertiary)', borderRadius: 'var(--border-radius-md)' }}>
            <span style={labelStyle}>AI Original</span>
            <div style={{ fontSize: '18px', fontWeight: 700 }}>
              {comparison.ai_original_amount !== null ? formatCurrency(comparison.ai_original_amount) : '—'}
            </div>
          </div>
          <div style={{ padding: '12px', backgroundColor: 'var(--bg-tertiary)', borderRadius: 'var(--border-radius-md)' }}>
            <span style={labelStyle}>Final RH</span>
            <div style={{ fontSize: '18px', fontWeight: 700 }}>
              {comparison.final_rh_amount !== null ? formatCurrency(comparison.final_rh_amount) : '—'}
            </div>
          </div>
          <div style={{ padding: '12px', backgroundColor: 'var(--bg-tertiary)', borderRadius: 'var(--border-radius-md)' }}>
            <span style={labelStyle}>Difference</span>
            <div style={{
              fontSize: '18px', fontWeight: 700,
              color: comparison.difference !== null && comparison.difference > 0 ? '#22c55e' : comparison.difference !== null && comparison.difference < 0 ? '#ef4444' : 'var(--text-primary)',
            }}>
              {comparison.difference !== null ? formatCurrency(comparison.difference) : '—'}
            </div>
          </div>
        </div>
      )}

      {(comparison?.ai_only_items.length ?? 0) > 0 || (comparison?.rh_only_items.length ?? 0) > 0 ? (
        <div style={{ display: 'flex', gap: '24px' }}>
          {(comparison?.ai_only_items.length ?? 0) > 0 && (
            <div>
              <span style={{ ...labelStyle, color: '#ef4444' }}>AI Only (Removed)</span>
              <ul style={{ margin: '4px 0', paddingLeft: '20px', fontSize: '13px' }}>
                {comparison!.ai_only_items.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          )}
          {(comparison?.rh_only_items.length ?? 0) > 0 && (
            <div>
              <span style={{ ...labelStyle, color: '#22c55e' }}>RH Only (Added)</span>
              <ul style={{ margin: '4px 0', paddingLeft: '20px', fontSize: '13px' }}>
                {comparison!.rh_only_items.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          )}
        </div>
      ) : null}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
        <div>
          <h4 style={{ fontSize: '13px', fontWeight: 600, margin: '0 0 8px 0' }}>AI-Generated Line Items</h4>
          {aiItems.length === 0 ? (
            <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>No AI line items</span>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
              <thead>
                <tr>
                  <th style={{ textAlign: 'left', padding: '4px 8px', borderBottom: '1px solid var(--border-color)' }}>Item</th>
                  <th style={{ textAlign: 'right', padding: '4px 8px', borderBottom: '1px solid var(--border-color)' }}>Qty</th>
                  <th style={{ textAlign: 'right', padding: '4px 8px', borderBottom: '1px solid var(--border-color)' }}>Rate</th>
                  <th style={{ textAlign: 'right', padding: '4px 8px', borderBottom: '1px solid var(--border-color)' }}>Total</th>
                </tr>
              </thead>
              <tbody>
                {aiItems.map((item, i) => (
                  <tr key={i}>
                    <td style={{ padding: '4px 8px', borderBottom: '1px solid var(--border-color)' }}>{item.item || '—'}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right', borderBottom: '1px solid var(--border-color)' }}>{item.quantity ?? '—'}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right', borderBottom: '1px solid var(--border-color)' }}>{item.rate !== null ? formatCurrency(item.rate) : '—'}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right', borderBottom: '1px solid var(--border-color)' }}>
                      {item.line_item_total !== null ? formatCurrency(item.line_item_total) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div>
          <h4 style={{ fontSize: '13px', fontWeight: 600, margin: '0 0 8px 0' }}>Final RH Line Items</h4>
          {finalItems.length === 0 ? (
            <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>No final line items</span>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
              <thead>
                <tr>
                  <th style={{ textAlign: 'left', padding: '4px 8px', borderBottom: '1px solid var(--border-color)' }}>Item</th>
                  <th style={{ textAlign: 'right', padding: '4px 8px', borderBottom: '1px solid var(--border-color)' }}>Qty</th>
                  <th style={{ textAlign: 'right', padding: '4px 8px', borderBottom: '1px solid var(--border-color)' }}>Rate</th>
                  <th style={{ textAlign: 'right', padding: '4px 8px', borderBottom: '1px solid var(--border-color)' }}>Total</th>
                </tr>
              </thead>
              <tbody>
                {finalItems.map((item) => (
                  <tr key={item.claim_service_id}>
                    <td style={{ padding: '4px 8px', borderBottom: '1px solid var(--border-color)' }}>{item.item || '—'}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right', borderBottom: '1px solid var(--border-color)' }}>{item.quantity ?? '—'}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right', borderBottom: '1px solid var(--border-color)' }}>{item.rate !== null ? formatCurrency(item.rate) : '—'}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right', borderBottom: '1px solid var(--border-color)' }}>
                      {item.rate !== null && item.quantity !== null ? formatCurrency(item.rate * item.quantity) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Main trace component
// ---------------------------------------------------------------------------

export const AiInvoiceTrace: React.FC<Props> = ({ claimId, onBack }) => {
  const [trace, setTrace] = useState<AiInvoiceTraceType | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    aiAnalyticsApi
      .getInvoiceTrace(claimId)
      .then((res) => {
        if (active) {
          setTrace(res);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (active) {
          setError(err.message || 'Failed to load invoice trace.');
          setLoading(false);
        }
      });
    return () => { active = false; };
  }, [claimId]);

  if (loading) return <LoadingState label={`Loading trace for claim ${claimId}…`} />;
  if (error) return <ErrorState message={error} />;
  if (!trace) return null;

  return (
    <div style={billingStyles.page}>
      {/* Back button + header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
        <button
          onClick={onBack}
          style={{
            display: 'flex', alignItems: 'center', gap: '6px', padding: '8px 12px',
            border: '1px solid var(--border-color)', borderRadius: 'var(--border-radius-md)',
            backgroundColor: 'var(--bg-primary)', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: '13px',
          }}
        >
          <ArrowLeft size={14} /> Back to Cohort
        </button>
        <div>
          <h2 style={{ fontSize: '18px', fontWeight: 700, margin: 0 }}>
            Claim {trace.claim_id} — Invoice Trace
          </h2>
          <span style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
            {trace.department_name || `Dept ${trace.department_id}`} · Run #{trace.run_number || '—'}
          </span>
        </div>
      </div>

      {/* Summary header */}
      <div style={billingStyles.card}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '16px' }}>
          <div>
            <span style={labelStyle}>Business Outcome</span>
            <div><span style={outcomeBadgeStyle(trace.business_outcome)}>{trace.business_outcome.replace(/_/g, ' ')}</span></div>
          </div>
          <div>
            <span style={labelStyle}>AI Status</span>
            <div style={valueStyle}>{trace.claim_processing_status || '—'}</div>
          </div>
          <div>
            <span style={labelStyle}>Confidence</span>
            <div style={valueStyle}>{trace.confidence_level !== null ? `${trace.confidence_level}%` : '—'}</div>
          </div>
          <div>
            <span style={labelStyle}>Writeback</span>
            <div style={valueStyle}>{trace.line_items_save_to_rh_status === true ? 'Success' : trace.line_items_save_to_rh_status === false ? 'Not Saved' : '—'}</div>
          </div>
          <div>
            <span style={labelStyle}>Invoice Total</span>
            <div style={valueStyle}>{trace.invoice_total !== null ? formatCurrency(trace.invoice_total) : '—'}</div>
          </div>
          <div>
            <span style={labelStyle}>Processing Time</span>
            <div style={valueStyle}>{trace.processing_time_seconds !== null ? `${trace.processing_time_seconds.toFixed(1)}s` : '—'}</div>
          </div>
          <div>
            <span style={labelStyle}>Retries</span>
            <div style={valueStyle}>{trace.retry_count}</div>
          </div>
          <div>
            <span style={labelStyle}>Billing Category</span>
            <div style={valueStyle}>{trace.billing_category || '—'}</div>
          </div>
        </div>

        {trace.cancellation_reason && (
          <div style={{ marginTop: '16px', padding: '12px', backgroundColor: 'rgba(239, 68, 68, 0.1)', borderRadius: 'var(--border-radius-md)' }}>
            <span style={{ ...labelStyle, color: '#ef4444' }}>Cancellation Reason</span>
            <div style={{ fontSize: '14px', fontWeight: 600, color: '#ef4444' }}>{trace.cancellation_reason}</div>
            {trace.cancellation_description && (
              <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>
                {trace.cancellation_description}
              </div>
            )}
          </div>
        )}

        {trace.review_msg && (
          <div style={{ marginTop: '16px' }}>
            <span style={labelStyle}>AI Review Message</span>
            <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginTop: '4px', lineHeight: 1.5 }}>
              {trace.review_msg}
            </div>
          </div>
        )}
      </div>

      {/* Timeline */}
      <div style={billingStyles.card}>
        <h3 style={sectionTitleStyle}><Clock size={16} /> Timeline</h3>
        <AiInvoiceTimeline trace={trace} />
      </div>

      {/* Line item comparison */}
      <div style={billingStyles.card}>
        <h3 style={sectionTitleStyle}><GitCompare size={16} /> Line Item Comparison</h3>
        <AiLineItemComparisonView
          aiItems={trace.ai_line_items}
          finalItems={trace.final_line_items}
          comparison={trace.comparison}
        />
      </div>

      {/* Agent conversations */}
      <div style={billingStyles.card}>
        <h3 style={sectionTitleStyle}><MessageSquare size={16} /> Agent Conversations ({trace.conversations.length})</h3>
        <AiConversationViewer conversations={trace.conversations} />
      </div>

      {/* Data completeness */}
      {!trace.data_complete && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: '12px', padding: '12px 16px',
          backgroundColor: 'rgba(234, 179, 8, 0.1)', border: '1px solid rgba(234, 179, 8, 0.2)',
          borderRadius: 'var(--border-radius-md)', color: '#eab308', fontSize: '13px',
        }}>
          <span>Data may be incomplete. Source status: {Object.entries(trace.source_status).map(([k, v]) => `${k}=${v}`).join(', ')}</span>
        </div>
      )}
    </div>
  );
};
