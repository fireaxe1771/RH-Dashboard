import React, { useState } from 'react';
import { Send, Sparkles, ChevronDown, ChevronUp, RefreshCw } from 'lucide-react';
import { billingApi, AIQuerySource } from '../../services/billingApi';
import { billingStyles, formatPercent } from './shared';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  sources?: AIQuerySource[];
  isLoading?: boolean;
  error?: string;
  timestamp: Date;
}

const EXAMPLE_QUESTIONS = [
  'What are my top spending services this month?',
  'Where can I save the most money right now?',
  'Are any of my budgets at risk of being exceeded?',
  'What reserved instances should I purchase?',
  'Show me any unusual cost spikes this month.',
];

function newId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

const SourceList: React.FC<{ sources: AIQuerySource[] }> = ({ sources }) => {
  const [open, setOpen] = useState(false);
  if (sources.length === 0) return null;
  return (
    <div style={{ marginTop: '10px' }}>
      <button
        onClick={() => setOpen(!open)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '4px',
          background: 'none',
          border: 'none',
          color: 'var(--accent-primary)',
          cursor: 'pointer',
          fontSize: '12px',
          fontWeight: 600,
          padding: 0,
        }}
      >
        {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        Sources ({sources.length})
      </button>
      {open && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginTop: '8px' }}>
          {sources.map((s, i) => (
            <div
              key={i}
              style={{
                fontSize: '12px',
                color: 'var(--text-secondary)',
                padding: '6px 10px',
                backgroundColor: 'var(--bg-tertiary)',
                borderRadius: 'var(--border-radius-sm)',
              }}
            >
              <strong>{s.document_type}</strong>
              {s.period ? ` · ${s.period}` : ''}
              {s.dimension_value ? ` · ${s.dimension_value}` : ''}
              {' · '}
              {formatPercent(s.score * 100)} relevance
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export const AICostAnalyst: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isQuerying, setIsQuerying] = useState(false);

  const submitQuestion = async (question: string) => {
    const trimmed = question.trim();
    if (!trimmed || isQuerying) return;

    const userMsg: Message = { id: newId(), role: 'user', content: trimmed, timestamp: new Date() };
    const loadingMsg: Message = { id: newId(), role: 'assistant', content: '', isLoading: true, timestamp: new Date() };
    setMessages((prev) => [...prev, userMsg, loadingMsg]);
    setInputValue('');
    setIsQuerying(true);

    try {
      const res = await billingApi.aiQuery({ question: trimmed });
      setMessages((prev) =>
        prev.map((m) =>
          m.id === loadingMsg.id
            ? { ...m, content: res.answer, sources: res.sources, isLoading: false }
            : m,
        ),
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to get a response.';
      setMessages((prev) =>
        prev.map((m) => (m.id === loadingMsg.id ? { ...m, isLoading: false, error: message } : m)),
      );
    } finally {
      setIsQuerying(false);
    }
  };

  const retry = (failedId: string) => {
    const idx = messages.findIndex((m) => m.id === failedId);
    const userMsg = idx > 0 ? messages[idx - 1] : null;
    if (userMsg && userMsg.role === 'user') {
      setMessages((prev) => prev.filter((m) => m.id !== failedId && m.id !== userMsg.id));
      submitQuestion(userMsg.content);
    }
  };

  return (
    <div style={{ ...billingStyles.card, display: 'flex', flexDirection: 'column', height: '100%', minHeight: '520px', padding: 0 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '16px 20px',
          borderBottom: '1px solid var(--border-color)',
        }}
      >
        <Sparkles size={18} style={{ color: 'var(--accent-primary)' }} />
        <span style={{ fontWeight: 700, color: 'var(--text-primary)' }}>AI Cost Analyst</span>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '20px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
        {messages.length === 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <span style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>
              Ask anything about your Azure spending. Try one of these:
            </span>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
              {EXAMPLE_QUESTIONS.map((q) => (
                <button
                  key={q}
                  onClick={() => submitQuestion(q)}
                  style={{
                    padding: '8px 12px',
                    fontSize: '13px',
                    borderRadius: 'var(--border-radius-md)',
                    border: '1px solid var(--border-color)',
                    backgroundColor: 'var(--bg-tertiary)',
                    color: 'var(--text-primary)',
                    cursor: 'pointer',
                    textAlign: 'left',
                  }}
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((msg) => (
            <div
              key={msg.id}
              style={{
                display: 'flex',
                justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
              }}
            >
              <div
                style={{
                  maxWidth: '80%',
                  padding: '12px 16px',
                  borderRadius: 'var(--border-radius-lg)',
                  fontSize: '14px',
                  lineHeight: 1.5,
                  whiteSpace: 'pre-wrap',
                  backgroundColor: msg.role === 'user' ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
                  color: msg.role === 'user' ? '#fff' : 'var(--text-primary)',
                }}
              >
                {msg.isLoading ? (
                  <span className="loader-dots" style={{ color: 'var(--text-secondary)' }}>Analyzing…</span>
                ) : msg.error ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    <span style={{ color: 'var(--color-danger)' }}>{msg.error}</span>
                    <button
                      onClick={() => retry(msg.id)}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px',
                        background: 'none',
                        border: '1px solid var(--border-color)',
                        borderRadius: 'var(--border-radius-sm)',
                        color: 'var(--text-primary)',
                        cursor: 'pointer',
                        fontSize: '12px',
                        padding: '4px 8px',
                        alignSelf: 'flex-start',
                      }}
                    >
                      <RefreshCw size={12} /> Retry
                    </button>
                  </div>
                ) : (
                  <>
                    {msg.content}
                    {msg.sources && <SourceList sources={msg.sources} />}
                  </>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          submitQuestion(inputValue);
        }}
        style={{
          display: 'flex',
          gap: '10px',
          padding: '16px 20px',
          borderTop: '1px solid var(--border-color)',
        }}
      >
        <input
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          placeholder="Ask about your Azure costs…"
          aria-label="Ask about your Azure costs"
          style={{
            flex: 1,
            padding: '10px 14px',
            fontSize: '14px',
            borderRadius: 'var(--border-radius-md)',
            border: '1px solid var(--border-color)',
            backgroundColor: 'var(--bg-primary)',
            color: 'var(--text-primary)',
          }}
        />
        <button
          type="submit"
          disabled={isQuerying || !inputValue.trim()}
          className="btn btn-primary"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '10px 16px',
            opacity: isQuerying || !inputValue.trim() ? 0.6 : 1,
            cursor: isQuerying || !inputValue.trim() ? 'not-allowed' : 'pointer',
          }}
        >
          <Send size={16} /> Send
        </button>
      </form>
    </div>
  );
};
