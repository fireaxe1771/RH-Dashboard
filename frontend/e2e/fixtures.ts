/**
 * Shared Playwright fixtures: API mocking helpers for all /api/* endpoints.
 *
 * Every E2E test uses these fixtures to intercept network calls so no real
 * backend is required. Tests can override individual mock responses as needed.
 */
import { test as base, expect, type Page, type Route } from '@playwright/test';

// ---------------------------------------------------------------------------
// Mock data
// ---------------------------------------------------------------------------

export const MOCK_DASHBOARDS = [
  {
    _id: 'dash-001',
    id: 'dash-001',
    name: 'Fire Claims Overview',
    description: 'Monthly fire claims summary',
    created_by: 'dev.local@streamlineas.com',
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
    widgets: [
      {
        id: 'w1',
        title: 'Total Claims',
        type: 'stat',
        sql_query: 'SELECT COUNT(*) as total FROM claims',
        layout: { x: 0, y: 0, w: 4, h: 2 },
        config: { format: 'number' },
      },
      {
        id: 'w2',
        title: 'Claims by Department',
        type: 'bar',
        sql_query: 'SELECT department, COUNT(*) as count FROM claims GROUP BY department',
        layout: { x: 4, y: 0, w: 8, h: 4 },
        config: { xAxisKey: 'department', yAxisKeys: ['count'] },
      },
    ],
  },
];

export const MOCK_QUERY_RESULT = {
  columns: ['total'],
  rows: [{ total: 142 }],
};

export const MOCK_BAR_RESULT = {
  columns: ['department', 'count'],
  rows: [
    { department: 'Fire', count: 50 },
    { department: 'Police', count: 30 },
    { department: 'EMS', count: 62 },
  ],
};

export const MOCK_FILTERS = {
  departments: [
    { id: '1', name: 'Fire Department' },
    { id: '2', name: 'Police Department' },
  ],
  processors: [
    { id: 'p1', name: 'Processor A' },
  ],
  claimTypes: ['Fire', 'Water', 'Smoke'],
};

export const MOCK_SERVER_DATE = '2026-06-07';

// Billing mock data

export const MOCK_COST_SUMMARY = {
  items: [
    { period: '2026-06', dimension: 'ServiceName', dimension_value: 'Virtual Machines', total_cost: 4200, currency: 'USD', change_pct: 12.5, change_amount: 500, record_count: 10 },
    { period: '2026-06', dimension: 'ServiceName', dimension_value: 'Storage', total_cost: 800, currency: 'USD', change_pct: -3.0, change_amount: -25, record_count: 5 },
  ],
  total: 5000,
  currency: 'USD',
  period: '2026-06',
};

export const MOCK_TOP_SPENDERS = [
  { period: '2026-06', dimension: 'ServiceName', dimension_value: 'Virtual Machines', total_cost: 4200, currency: 'USD', change_pct: 12.5, change_amount: 500, record_count: 10 },
  { period: '2026-06', dimension: 'ServiceName', dimension_value: 'Storage', total_cost: 800, currency: 'USD', change_pct: -3.0, change_amount: -25, record_count: 5 },
  { period: '2026-06', dimension: 'ServiceName', dimension_value: 'Network', total_cost: 300, currency: 'USD', change_pct: null, change_amount: null, record_count: 2 },
];

export const MOCK_COST_TREND = [
  { period: '2026-04', dimension_value: 'Virtual Machines', total_cost: 3500, currency: 'USD' },
  { period: '2026-05', dimension_value: 'Virtual Machines', total_cost: 3800, currency: 'USD' },
  { period: '2026-06', dimension_value: 'Virtual Machines', total_cost: 4200, currency: 'USD' },
  { period: '2026-04', dimension_value: 'Storage', total_cost: 700, currency: 'USD' },
  { period: '2026-05', dimension_value: 'Storage', total_cost: 750, currency: 'USD' },
  { period: '2026-06', dimension_value: 'Storage', total_cost: 800, currency: 'USD' },
];

export const MOCK_BUDGETS = [
  { budget_id: 'b1', budget_name: 'Production Budget', scope: '/subscriptions/abc', amount: 10000, current_spend: 8000, forecast_spend: 9000, utilization_pct: 80, time_grain: 'Monthly', currency: 'USD' },
  { budget_id: 'b2', budget_name: 'Dev Budget', scope: '/subscriptions/def', amount: 5000, current_spend: 2000, forecast_spend: 2500, utilization_pct: 40, time_grain: 'Monthly', currency: 'USD' },
];

export const MOCK_ALERTS = [
  { alert_id: 'a1', alert_name: 'Budget Exceeded', alert_type: 'Threshold', status: 'Active', description: 'Prod budget exceeded', budget_name: 'Prod', current_spend: 10100, threshold: 10000, currency: 'USD', creation_time: '2026-06-01' },
];

export const MOCK_ADVISOR_SUMMARY = {
  total_recommendations: 5,
  cost_recommendations: 3,
  total_monthly_savings: 750,
  currency: 'USD',
  by_impact: { High: 2, Medium: 2, Low: 1 },
};

export const MOCK_ADVISOR_RECS = [
  {
    recommendation_id: 'rec-1',
    category: 'Cost',
    impact: 'High',
    impacted_value: 'vm-prod-01',
    resource_group: 'rg-prod',
    problem_description: 'This VM is underutilized and can be resized to save money.',
    solution_description: 'Resize to Standard_D2s_v5.',
    estimated_monthly_savings: 320,
    savings_currency: 'USD',
    current_sku: 'Standard_D8s_v5',
    recommended_sku: 'Standard_D2s_v5',
    last_updated: '2026-06-01',
    status: 'Active',
  },
  {
    recommendation_id: 'rec-2',
    category: 'Security',
    impact: 'Medium',
    impacted_value: 'storage-acc-01',
    resource_group: 'rg-prod',
    problem_description: 'Storage account allows public blob access.',
    solution_description: 'Disable public access on the blob container.',
    estimated_monthly_savings: null,
    savings_currency: null,
    current_sku: null,
    recommended_sku: null,
    last_updated: '2026-06-01',
    status: 'Active',
  },
];

export const MOCK_INVOICES = [
  { invoice_id: 'inv-001', billing_period_start: '2026-05-01', billing_period_end: '2026-05-31', invoice_date: '2026-06-05', due_date: '2026-06-30', billed_amount: 5000, amount_due: 0, billing_currency: 'USD', status: 'Paid', invoice_download_url: 'https://example.com/inv-001.pdf' },
  { invoice_id: 'inv-002', billing_period_start: '2026-04-01', billing_period_end: '2026-04-30', invoice_date: '2026-05-05', due_date: '2026-05-30', billed_amount: 4500, amount_due: 0, billing_currency: 'USD', status: 'Paid', invoice_download_url: null },
];

export const MOCK_RESERVATIONS = [
  { subscription_id: 'sub-1', sku_name: 'Standard_D2s_v5', resource_type: 'virtualMachines', scope: 'Shared', term: 'P1Y', look_back_period: 'Last30Days', location: 'eastus', recommended_quantity: 3, total_cost_with_no_ri: 12000, total_cost_with_ri: 8000, net_savings: 4000, currency: 'USD' },
  { subscription_id: 'sub-2', sku_name: 'Standard_F4s_v5', resource_type: 'virtualMachines', scope: 'Shared', term: 'P3Y', look_back_period: 'Last30Days', location: 'westus', recommended_quantity: 2, total_cost_with_no_ri: 9000, total_cost_with_ri: 5000, net_savings: 4000, currency: 'USD' },
];

export const MOCK_AI_RESPONSE = {
  answer: 'Your Virtual Machines are the top cost driver at $4,200 per month. Consider resizing underutilized VMs per Azure Advisor recommendations to save approximately $320/month.',
  sources: [
    { document_type: 'top_spenders', period: '2026-06', dimension_value: 'Virtual Machines', total_cost: 4200, score: 0.92 },
  ],
  model: 'gpt-4o-mini',
  question: 'What drives my Azure costs?',
};

export const MOCK_SYNC_STATUS = {
  syncs: [
    { sync_type: 'cost_details_daily', status: 'completed', last_run: '2026-06-07T02:00:00Z', last_period: '2026-06', records_synced: 150, duration_seconds: 45, error_message: null },
  ],
};

// ---------------------------------------------------------------------------
// Route handler — intercepts all /api/* calls and returns mock data
// ---------------------------------------------------------------------------

type MockOverrides = Record<string, unknown>;

function matchApiRoute(url: string, pattern: string): boolean {
  const path = new URL(url, 'http://localhost').pathname;
  return path === pattern || path.startsWith(pattern + '?');
}

async function handleApiRoute(route: Route, overrides: MockOverrides = {}) {
  const url = new URL(route.request().url(), 'http://localhost');
  const path = url.pathname;
  const method = route.request().method();

  // Dashboard endpoints
  if (path === '/api/dashboards' && method === 'GET') {
    return route.fulfill({ status: 200, json: overrides.dashboards ?? MOCK_DASHBOARDS });
  }
  if (path === '/api/dashboards' && method === 'POST') {
    return route.fulfill({ status: 200, json: overrides.newDashboard ?? { ...MOCK_DASHBOARDS[0], _id: 'dash-new', name: 'New Dashboard' } });
  }

  // Query endpoints
  if (path === '/api/query/schema') {
    return route.fulfill({ status: 200, json: overrides.schema ?? { tables: [] } });
  }
  if (path === '/api/query/filters') {
    return route.fulfill({ status: 200, json: overrides.filters ?? MOCK_FILTERS });
  }
  if (path === '/api/query/execute') {
    return route.fulfill({ status: 200, json: overrides.queryResult ?? MOCK_QUERY_RESULT });
  }
  if (path === '/api/query/sql') {
    return route.fulfill({ status: 200, json: overrides.queryResult ?? MOCK_QUERY_RESULT });
  }
  if (path === '/api/query/drilldown') {
    return route.fulfill({ status: 200, json: overrides.drilldownResult ?? { columns: ['id', 'claim_number'], rows: [{ id: 1, claim_number: 'CLM-001' }] } });
  }
  if (path === '/api/filters/options') {
    return route.fulfill({ status: 200, json: overrides.filters ?? MOCK_FILTERS });
  }
  if (path === '/api/server-date') {
    return route.fulfill({ status: 200, json: overrides.serverDate ?? MOCK_SERVER_DATE });
  }

  // Billing: sync
  if (path === '/api/billing/sync/status') {
    return route.fulfill({ status: 200, json: overrides.syncStatus ?? MOCK_SYNC_STATUS });
  }
  if (path === '/api/billing/sync/trigger') {
    return route.fulfill({ status: 200, json: { status: 'queued', sync_type: 'daily' } });
  }

  // Billing: cost
  if (path === '/api/billing/cost/summary') {
    return route.fulfill({ status: 200, json: overrides.costSummary ?? MOCK_COST_SUMMARY });
  }
  if (path === '/api/billing/cost/trend') {
    return route.fulfill({ status: 200, json: overrides.costTrend ?? MOCK_COST_TREND });
  }
  if (path === '/api/billing/cost/top-spenders') {
    return route.fulfill({ status: 200, json: overrides.topSpenders ?? MOCK_TOP_SPENDERS });
  }
  if (path === '/api/billing/cost/daily') {
    return route.fulfill({ status: 200, json: overrides.dailyCosts ?? [{ date: '2026-06-01', total_cost: 150 }] });
  }
  if (path === '/api/billing/cost/forecast') {
    return route.fulfill({ status: 200, json: overrides.costForecast ?? [] });
  }
  if (path === '/api/billing/cost/by-tag') {
    return route.fulfill({ status: 200, json: overrides.costByTag ?? [{ tag_value: 'prod', total_cost: 5000 }] });
  }

  // Billing: budgets & alerts
  if (path === '/api/billing/budgets') {
    return route.fulfill({ status: 200, json: overrides.budgets ?? MOCK_BUDGETS });
  }
  if (path === '/api/billing/alerts') {
    return route.fulfill({ status: 200, json: overrides.alerts ?? MOCK_ALERTS });
  }

  // Billing: advisor
  if (path === '/api/billing/advisor/summary') {
    return route.fulfill({ status: 200, json: overrides.advisorSummary ?? MOCK_ADVISOR_SUMMARY });
  }
  if (path === '/api/billing/advisor/recommendations') {
    return route.fulfill({ status: 200, json: overrides.advisorRecs ?? MOCK_ADVISOR_RECS });
  }
  if (path === '/api/billing/advisor/cost-savings') {
    return route.fulfill({ status: 200, json: overrides.advisorCostSavings ?? MOCK_ADVISOR_RECS.filter(r => r.category === 'Cost') });
  }

  // Billing: invoices
  if (path === '/api/billing/invoices' && method === 'GET') {
    return route.fulfill({ status: 200, json: overrides.invoices ?? MOCK_INVOICES });
  }
  if (path.startsWith('/api/billing/invoices/') && method === 'GET') {
    const id = path.split('/').pop();
    const invoice = (overrides.invoices ?? MOCK_INVOICES).find((i: any) => i.invoice_id === id);
    if (invoice) return route.fulfill({ status: 200, json: invoice });
    return route.fulfill({ status: 404, json: { detail: 'Invoice not found.' } });
  }

  // Billing: reservations
  if (path === '/api/billing/reservations/details') {
    return route.fulfill({ status: 200, json: overrides.reservationDetails ?? [] });
  }
  if (path === '/api/billing/reservations/recommendations') {
    return route.fulfill({ status: 200, json: overrides.reservations ?? MOCK_RESERVATIONS });
  }

  // Billing: AI
  if (path === '/api/billing/ai/query') {
    return route.fulfill({ status: 200, json: overrides.aiResponse ?? MOCK_AI_RESPONSE });
  }

  // Fallback: unhandled API route
  console.warn(`[E2E mock] Unhandled API route: ${method} ${path}`);
  return route.fulfill({ status: 404, json: { detail: 'Not mocked' } });
}

// ---------------------------------------------------------------------------
// Test fixture extension
// ---------------------------------------------------------------------------

export const test = base.extend<{
  mockApi: (overrides?: MockOverrides) => Promise<void>;
}>({
  mockApi: async ({ page }, use) => {
    await use(async (overrides: MockOverrides = {}) => {
      await page.route('**/api/**', (route) => handleApiRoute(route, overrides));
    });
  },
});

export { expect };
