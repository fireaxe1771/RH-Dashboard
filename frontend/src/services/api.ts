/**
 * API client service for talking to the FastAPI backend.
 */

import { AccountInfo, IPublicClientApplication } from '@azure/msal-browser';
import { loginRequest } from '../authConfig';

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

let activeToken: string | null = null;

// Reference to the MSAL instance, set by setMsalInstance(). Used to
// silently refresh expired tokens when the API returns 401.
let msalInstance: IPublicClientApplication | null = null;
let msalAccount: AccountInfo | null = null;

/**
 * Sets the MSAL instance and active account so the API layer can
 * silently refresh expired tokens on 401 responses.
 */
export function setMsalInstance(instance: IPublicClientApplication, account: AccountInfo | null): void {
  msalInstance = instance;
  msalAccount = account;
}

/**
 * Sets the active OAuth bearer token for MSAL authenticated queries.
 */
export function setAuthToken(token: string | null): void {
  activeToken = token;
}

/**
 * Returns the active OAuth bearer token (shared with the billing API client).
 */
export function getAuthToken(): string | null {
  return activeToken;
}

/**
 * Attempts to silently refresh the idToken via MSAL. Returns the new token
 * or null if refresh fails (caller should let the 401 propagate).
 */
async function refreshToken(): Promise<string | null> {
  if (!msalInstance || !msalAccount) return null;
  try {
    const response = await msalInstance.acquireTokenSilent({
      ...loginRequest,
      account: msalAccount,
    });
    if (response.idToken) {
      activeToken = response.idToken;
      return response.idToken;
    }
  } catch (error) {
    console.error("Silent token refresh failed on 401:", error);
  }
  return null;
}

/**
 * Helper to build headers with authentication tokens if available.
 */
function getHeaders(): HeadersInit {
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  };
  
  if (activeToken) {
    headers['Authorization'] = `Bearer ${activeToken}`;
  }
  
  return headers;
}

/**
 * Custom fetch wrapper to handle errors consistently.
 * On 401, attempts a silent token refresh and retries the request once.
 */
async function fetchJson<T>(url: string, options: RequestInit = {}): Promise<T> {
  const doFetch = async (): Promise<Response> => {
    return fetch(url, {
      ...options,
      headers: {
        ...getHeaders(),
        ...options.headers,
      },
    });
  };

  let response = await doFetch();

  // If we got a 401 and we have an MSAL instance, try refreshing the token
  // and retrying once. This handles expired tokens gracefully without
  // bouncing the user to a full re-login.
  if (response.status === 401 && msalInstance) {
    const newToken = await refreshToken();
    if (newToken) {
      response = await doFetch();
    }
  }

  if (!response.ok) {
    let errorMessage = `HTTP Error ${response.status}`;
    try {
      const errorData = await response.json();
      errorMessage = errorData.detail || errorMessage;
    } catch {
      // JSON parsing failed, keep basic message
    }
    throw new Error(errorMessage);
  }

  return response.json() as Promise<T>;
}

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
