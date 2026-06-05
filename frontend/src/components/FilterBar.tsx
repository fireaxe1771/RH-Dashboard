import React, { useEffect, useState } from 'react';
import { api, FilterOptions } from '../services/api';
import { Calendar, Filter, Users, ShieldAlert, AlertCircle } from 'lucide-react';

export interface DashboardFilters {
  department_id?: string;
  processor_id?: string;
  start_date?: string;
  end_date?: string;
}

interface FilterBarProps {
  filters: DashboardFilters;
  onChange: (filters: DashboardFilters) => void;
}

export const FilterBar: React.FC<FilterBarProps> = ({ filters, onChange }) => {
  const [options, setOptions] = useState<FilterOptions>({
    departments: [],
    processors: [],
    claimTypes: []
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api.getFilterOptions()
      .then((data) => {
        if (active) {
          setOptions(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        console.error("Failed to load filter options:", err);
        if (active) {
          setError(err.message || "Failed to load database filter options from server.");
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, []);

  const handleSelectChange = (key: keyof DashboardFilters, value: string) => {
    onChange({
      ...filters,
      [key]: value || undefined, // undefined strips it from requests if empty
    });
  };

  const handleDateChange = (key: 'start_date' | 'end_date', value: string) => {
    onChange({
      ...filters,
      [key]: value || undefined,
    });
  };

  if (error) {
    return (
      <div 
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
          padding: '16px 24px',
          backgroundColor: 'rgba(239, 68, 68, 0.1)',
          borderRadius: 'var(--border-radius-md)',
          border: '1px solid rgba(239, 68, 68, 0.2)',
          color: 'var(--color-danger)',
          fontSize: '14px',
          fontWeight: 500
        }}
      >
        <AlertCircle size={20} />
        <span><strong>Configuration/Connection Error:</strong> {error}</span>
      </div>
    );
  }

  return (
    <div 
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: '16px',
        alignItems: 'center',
        padding: '16px 24px',
        backgroundColor: 'var(--bg-secondary)',
        borderRadius: 'var(--border-radius-md)',
        border: '1px solid var(--border-color)',
        boxShadow: 'var(--shadow-sm)'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-secondary)', fontSize: '14px', fontWeight: 500 }}>
        <Filter size={16} style={{ color: 'var(--accent-primary)' }} />
        <span>Filters:</span>
      </div>

      {/* Department Dropdown */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', minWidth: '200px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
          <ShieldAlert size={10} />
          <span>Fire Department</span>
        </div>
        <select
          className="input"
          value={filters.department_id || ''}
          onChange={(e) => handleSelectChange('department_id', e.target.value)}
          style={{ padding: '8px 12px', fontSize: '13px' }}
          disabled={loading}
        >
          <option value="">All Departments</option>
          {options.departments.map((dept) => (
            <option key={dept.id} value={dept.id}>{dept.name}</option>
          ))}
        </select>
      </div>

      {/* Claims Processor Dropdown */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', minWidth: '200px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
          <Users size={10} />
          <span>Claims Processor</span>
        </div>
        <select
          className="input"
          value={filters.processor_id || ''}
          onChange={(e) => handleSelectChange('processor_id', e.target.value)}
          style={{ padding: '8px 12px', fontSize: '13px' }}
          disabled={loading}
        >
          <option value="">All Processors</option>
          {options.processors.map((proc) => (
            <option key={proc.id} value={proc.id}>{proc.name}</option>
          ))}
        </select>
      </div>

      {/* Date Selectors */}
      <div style={{ display: 'flex', gap: '12px' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', width: '150px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
            <Calendar size={10} />
            <span>Start Date</span>
          </div>
          <input
            type="date"
            className="input"
            value={filters.start_date || ''}
            onChange={(e) => handleDateChange('start_date', e.target.value)}
            style={{ padding: '7px 12px', fontSize: '13px' }}
            disabled={loading}
          />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', width: '150px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
            <Calendar size={10} />
            <span>End Date</span>
          </div>
          <input
            type="date"
            className="input"
            value={filters.end_date || ''}
            onChange={(e) => handleDateChange('end_date', e.target.value)}
            style={{ padding: '7px 12px', fontSize: '13px' }}
            disabled={loading}
          />
        </div>
      </div>
    </div>
  );
};
