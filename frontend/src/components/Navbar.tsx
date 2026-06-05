import React from 'react';
import { Database, RefreshCw, AlertCircle } from 'lucide-react';

interface NavbarProps {
  title: string;
  description?: string;
  isConnecting?: boolean;
  onRefresh?: () => void;
  dbStatus?: 'connected' | 'error' | 'testing';
}

export const Navbar: React.FC<NavbarProps> = ({
  title,
  description,
  isConnecting = false,
  onRefresh,
  dbStatus = 'connected'
}) => {
  return (
    <header className="navbar" data-testid="navbar">
      <div>
        <h1 className="navbar-title">{title}</h1>
        {description && (
          <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>
            {description}
          </p>
        )}
      </div>

      <div className="navbar-actions">
        {onRefresh && (
          <button 
            className="btn" 
            onClick={onRefresh}
            disabled={isConnecting}
            style={{ padding: '8px 12px', display: 'flex', alignItems: 'center', gap: '6px' }}
          >
            <RefreshCw size={14} className={isConnecting ? 'loader' : ''} />
            <span style={{ fontSize: '13px' }}>Sync</span>
          </button>
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px' }}>
          <Database size={16} className={dbStatus === 'connected' ? 'trend-up' : 'trend-down'} />
          <span style={{ color: 'var(--text-secondary)' }}>Target SQL:</span>
          {dbStatus === 'connected' && (
            <span style={{ color: 'var(--color-success)', display: 'flex', alignItems: 'center', gap: '4px', fontWeight: 500 }}>
              <span style={{ display: 'inline-block', width: '8px', height: '8px', borderRadius: '50%', backgroundColor: 'var(--color-success)' }} />
              Live Online
            </span>
          )}
          {dbStatus === 'testing' && (
            <span style={{ color: 'var(--color-warning)', display: 'flex', alignItems: 'center', gap: '4px' }}>
              <span className="loader" style={{ width: '8px', height: '8px', border: '1px solid transparent', borderTopColor: 'var(--color-warning)' }} />
              Connecting...
            </span>
          )}
          {dbStatus === 'error' && (
            <span style={{ color: 'var(--color-danger)', display: 'flex', alignItems: 'center', gap: '4px', fontWeight: 500 }}>
              <AlertCircle size={12} />
              Disconnected
            </span>
          )}
        </div>
      </div>
    </header>
  );
};
