/**
 * API client service for talking to the FastAPI backend.
 *
 * Auth-header injection, 401 silent-refresh-retry, and error parsing are
 * provided by the shared fetchWrapper. The token/MSAL setters are
 * re-exported here so existing imports from 'services/api' keep working.
 */

import { createApiFetch, setAuthToken, getAuthToken, setMsalInstance } from './fetchWrapper';

export { setAuthToken, getAuthToken, setMsalInstance };

export interface WidgetLayout {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface WidgetConfig {
  xAxisKey?: string;
  yAxisKeys?: string[];
  colors?: string[];
  embedUrl?: string;
  format?: string;
}

export interface Widget {
  id: string;
  title: string;
  type: 'stat' | 'line' | 'bar' | 'pie' | 'table' | 'looker';
  sql_query?: string;
  layout: WidgetLayout;
  config: WidgetConfig;
}

export interface Dashboard {
  id?: string;
  _id?: string; // MongoDB object ID
  name: string;
  description?: string;
  created_by?: string;
  created_at?: string;
  updated_at?: string;
  widgets: Widget[];
}

export interface QueryResult {
  columns: string[];
  rows: Record<string, unknown>[];
}

export interface FilterOptions {
  departments: { id: string; name: string }[];
  processors: { id: string; name: string }[];
  claimTypes: string[];
}

export interface SQLTableColumn {
  name: string;
  type: string;
}

export interface SQLTableSchema {
  table: string;
  columns: SQLTableColumn[];
}

// Shared fetch wrapper with 401 silent-refresh-retry and consistent error
// parsing. Bound to an empty base so the existing full-path call sites
// (e.g. '/api/dashboards') remain unchanged.
const fetchJson = createApiFetch('');

export const api = {
  /**
   * Fetch all dashboards.
   */
  getDashboards: async (): Promise<Dashboard[]> => {
    return fetchJson<Dashboard[]>('/api/dashboards');
  },

  /**
   * Fetch a dashboard by ID.
   */
  getDashboard: async (id: string): Promise<Dashboard> => {
    return fetchJson<Dashboard>(`/api/dashboards/${id}`);
  },

  /**
   * Create a new dashboard.
   */
  createDashboard: async (dashboard: Dashboard): Promise<Dashboard> => {
    return fetchJson<Dashboard>('/api/dashboards', {
      method: 'POST',
      body: JSON.stringify(dashboard),
    });
  },

  /**
   * Update an existing dashboard.
   */
  updateDashboard: async (id: string, dashboard: Dashboard): Promise<Dashboard> => {
    return fetchJson<Dashboard>(`/api/dashboards/${id}`, {
      method: 'PUT',
      body: JSON.stringify(dashboard),
    });
  },

  /**
   * Delete a dashboard.
   */
  deleteDashboard: async (id: string): Promise<{ success: boolean }> => {
    return fetchJson<{ success: boolean }>(`/api/dashboards/${id}`, {
      method: 'DELETE',
    });
  },

  /**
   * Run a SQL query against the target Azure SQL database with parameters.
   */
  runSqlQuery: async (
    sqlQuery: string,
    filters: {
      department_id?: string;
      processor_id?: string;
      start_date?: string;
      end_date?: string;
      range_type?: string;
      periods_back?: number;
    } = {}
  ): Promise<QueryResult> => {
    return fetchJson<QueryResult>('/api/query/sql', {
      method: 'POST',
      body: JSON.stringify({
        sql_query: sqlQuery,
        filters,
      }),
    });
  },

  /**
   * Get the SQL schema for autocomplete assistance in dashboard creation.
   */
  getSqlSchema: async (): Promise<SQLTableSchema[]> => {
    return fetchJson<SQLTableSchema[]>('/api/schema/sql');
  },

  /**
   * Get the database server's current date (from SQL Server GETDATE()).
   */
  getServerDate: async (): Promise<string> => {
    const result = await fetchJson<{ date: string }>('/api/server-date');
    return result.date;
  },

  /**
   * Get dropdown filter options (departments, processors, claim types).
   */
  getFilterOptions: async (): Promise<FilterOptions> => {
    return fetchJson<FilterOptions>('/api/filters/options');
  },

  /**
   * Run a parameterized drill-down query for detailed claims listings.
   */
  getDrillDownData: async (
    fieldName: string,
    fieldValue: unknown,
    filters: {
      department_id?: string;
      processor_id?: string;
      start_date?: string;
      end_date?: string;
      range_type?: string;
      periods_back?: number;
    } = {}
  ): Promise<QueryResult> => {
    return fetchJson<QueryResult>('/api/query/drilldown', {
      method: 'POST',
      body: JSON.stringify({
        field_name: fieldName,
        field_value: fieldValue,
        filters,
      }),
    });
  }
};
