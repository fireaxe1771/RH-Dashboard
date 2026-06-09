/**
 * API client for the Azure Billing analytics endpoints (/api/billing/*).
 * Mirrors the structure of services/api.ts and reuses its bearer-token state.
 */
import { getAuthToken } from './api';

// --- Sync ---

export interface SyncStatusEntry {
  sync_type: string;
  status: string;
  last_run: string | null;
  last_period: string | null;
  records_synced: number;
  duration_seconds: number | null;
  error_message: string | null;
}

export interface SyncStatusResponse {
  syncs: SyncStatusEntry[];
}

export interface TriggerSyncRequest {
  sync_type: string;
  billing_period?: string;
}

// --- Cost ---

export interface CostSummaryItem {
  period: string;
  dimension: string;
  dimension_value: string;
  total_cost: number;
  currency: string;
  change_pct: number | null;
  change_amount: number | null;
  record_count: number;
}

export interface CostSummaryResponse {
  items: CostSummaryItem[];
  total: number;
  currency: string;
  period: string;
}

export interface CostTrendPoint {
  period: string;
  dimension_value: string;
  total_cost: number;
  currency: string;
}

export interface DailyCostPoint {
  date: string;
  total_cost: number;
}

// --- Budgets & Alerts ---

export interface BudgetItem {
  budget_id: string;
  budget_name: string;
  scope: string;
  amount: number;
  current_spend: number;
  current_spend_currency?: string;
  forecast_spend: number | null;
  utilization_pct: number;
  forecast_utilization_pct?: number | null;
  time_grain: string;
  currency?: string;
}

export interface AlertItem {
  alert_id: string;
  alert_name: string;
  alert_type: string;
  status: string;
  description: string;
  budget_name: string | null;
  current_spend: number | null;
  threshold: number | null;
  currency: string | null;
  creation_time: string;
}

// --- Advisor ---

export interface AdvisorRecommendation {
  recommendation_id: string;
  category: string;
  impact: 'High' | 'Medium' | 'Low';
  impacted_value: string;
  resource_group: string;
  problem_description: string;
  solution_description: string;
  estimated_monthly_savings: number | null;
  savings_currency: string | null;
  current_sku: string | null;
  recommended_sku: string | null;
  last_updated: string;
  status: string;
}

export interface AdvisorSummary {
  total_recommendations: number;
  cost_recommendations: number;
  total_monthly_savings: number;
  currency: string;
  by_impact: Record<string, number>;
}

// --- Invoices ---

export interface InvoiceItem {
  invoice_id: string;
  billing_period_start: string;
  billing_period_end: string;
  invoice_date: string;
  due_date: string | null;
  billed_amount: number;
  amount_due: number;
  billing_currency: string;
  status: string;
  invoice_download_url: string | null;
}

// --- Reservations ---

export interface ReservationRecommendation {
  subscription_id: string;
  sku_name: string;
  resource_type: string;
  scope: string;
  term: string;
  look_back_period: string;
  location: string;
  recommended_quantity: number;
  total_cost_with_no_ri: number;
  total_cost_with_ri: number;
  net_savings: number;
  currency: string;
}

// --- AI Query ---

export interface AIQuerySource {
  document_type: string;
  period: string | null;
  dimension_value: string | null;
  total_cost: number | null;
  score: number;
}

export interface AIQueryRequest {
  question: string;
  document_types?: string[];
  period_filter?: string;
  top_k?: number;
}

export interface AIQueryResponse {
  answer: string;
  sources: AIQuerySource[];
  model: string;
  question: string;
}

const BASE_URL = '/api/billing';

async function billingFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getAuthToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string>) || {}),
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${BASE_URL}${path}`, { ...options, headers });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const err = await response.json();
      detail = err.detail || detail;
    } catch {
      // keep default message
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const billingApi = {
  getSyncStatus: () => billingFetch<SyncStatusResponse>('/sync/status'),

  triggerSync: (req: TriggerSyncRequest) =>
    billingFetch<{ status: string; sync_type: string }>('/sync/trigger', {
      method: 'POST',
      body: JSON.stringify(req),
    }),

  getCostSummary: (period: string, dimension = 'ServiceName') =>
    billingFetch<CostSummaryResponse>(
      `/cost/summary?period=${encodeURIComponent(period)}&dimension=${encodeURIComponent(dimension)}`,
    ),

  getCostTrend: (months = 12, dimension = 'ServiceName') =>
    billingFetch<CostTrendPoint[]>(
      `/cost/trend?months=${months}&dimension=${encodeURIComponent(dimension)}`,
    ),

  getTopSpenders: (period: string, dimension = 'ServiceName', limit = 10) =>
    billingFetch<CostSummaryItem[]>(
      `/cost/top-spenders?period=${encodeURIComponent(period)}&dimension=${encodeURIComponent(dimension)}&limit=${limit}`,
    ),

  getDailyCosts: (startDate: string, endDate: string, serviceName?: string) => {
    const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
    if (serviceName) params.append('service_name', serviceName);
    return billingFetch<DailyCostPoint[]>(`/cost/daily?${params.toString()}`);
  },

  getBudgets: () => billingFetch<BudgetItem[]>('/budgets'),

  getAlerts: () => billingFetch<AlertItem[]>('/alerts'),

  getAdvisorRecommendations: (category?: string, impact?: string) => {
    const params = new URLSearchParams();
    if (category) params.append('category', category);
    if (impact) params.append('impact', impact);
    const qs = params.toString();
    return billingFetch<AdvisorRecommendation[]>(
      `/advisor/recommendations${qs ? `?${qs}` : ''}`,
    );
  },

  getAdvisorSummary: () => billingFetch<AdvisorSummary>('/advisor/summary'),

  getAdvisorCostSavings: () =>
    billingFetch<AdvisorRecommendation[]>('/advisor/cost-savings'),

  getInvoices: () => billingFetch<InvoiceItem[]>('/invoices'),

  getInvoice: (invoiceId: string) =>
    billingFetch<InvoiceItem>(`/invoices/${encodeURIComponent(invoiceId)}`),

  getReservationRecommendations: () =>
    billingFetch<ReservationRecommendation[]>('/reservations/recommendations'),

  aiQuery: (req: AIQueryRequest) =>
    billingFetch<AIQueryResponse>('/ai/query', {
      method: 'POST',
      body: JSON.stringify(req),
    }),
};
