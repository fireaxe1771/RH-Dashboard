# Supporting Doc 07 — Frontend Integration Guide

**Project:** RecoveryHub Dashboard System  
**Purpose:** TypeScript interface definitions, API client patterns, component specifications, and UI conventions for all new billing frontend components.

---

## 1. TypeScript Interface Definitions (`services/billingApi.ts`)

All interfaces go at the top of `billingApi.ts`, mirroring the backend Pydantic models.

```typescript
// --- Sync ---

export interface SyncStatusEntry {
  sync_type: string;
  status: string;
  last_run: string | null;          // ISO datetime string
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
  period: string;                   // "YYYY-MM"
  dimension_value: string;
  total_cost: number;
  currency: string;
}

// --- Budgets & Alerts ---

export interface BudgetItem {
  budget_name: string;
  scope: string;
  amount: number;
  current_spend: number;
  forecast_spend: number | null;
  utilization_pct: number;
  time_grain: string;
  currency: string;
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
  by_impact: { High: number; Medium: number; Low: number };
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
```

---

## 2. API Client (`services/billingApi.ts`)

Follow the exact same structure as `services/api.ts`. The `apiRequest` helper is the same — import the token management pattern from `api.ts`.

```typescript
const BASE_URL = '/api/billing';

// Reuse the same setAuthToken and internal fetch wrapper from api.ts
// billingApi.ts imports from api.ts for the token:
import { setAuthToken } from './api';  // token is managed by api.ts already

async function billingFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const token = /* get from authContext or shared state — same pattern as api.ts */;
  const response = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
      ...(options?.headers || {})
    }
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(err.detail || `HTTP ${response.status}`);
  }
  return response.json();
}

// API functions:
export const billingApi = {
  getSyncStatus: () => billingFetch<SyncStatusResponse>('/sync/status'),
  
  triggerSync: (req: TriggerSyncRequest) =>
    billingFetch<{ status: string; sync_type: string }>('/sync/trigger', {
      method: 'POST',
      body: JSON.stringify(req)
    }),
  
  getCostSummary: (period: string, dimension = 'ServiceName') =>
    billingFetch<CostSummaryResponse>(`/cost/summary?period=${period}&dimension=${dimension}`),
  
  getCostTrend: (months = 12, dimension = 'ServiceName') =>
    billingFetch<{ items: CostTrendPoint[] }>(`/cost/trend?months=${months}&dimension=${dimension}`),
  
  getTopSpenders: (period: string, dimension = 'ServiceName', limit = 10) =>
    billingFetch<CostSummaryResponse>(`/cost/top-spenders?period=${period}&dimension=${dimension}&limit=${limit}`),
  
  getBudgets: () => billingFetch<{ items: BudgetItem[] }>('/budgets'),
  
  getAlerts: () => billingFetch<{ items: AlertItem[] }>('/alerts'),
  
  getAdvisorRecommendations: (category?: string, impact?: string) => {
    const params = new URLSearchParams();
    if (category) params.append('category', category);
    if (impact) params.append('impact', impact);
    return billingFetch<{ items: AdvisorRecommendation[] }>(`/advisor/recommendations?${params}`);
  },
  
  getAdvisorSummary: () => billingFetch<AdvisorSummary>('/advisor/summary'),
  
  getAdvisorCostSavings: () =>
    billingFetch<{ items: AdvisorRecommendation[] }>('/advisor/cost-savings'),
  
  getInvoices: () => billingFetch<{ items: InvoiceItem[] }>('/invoices'),
  
  getInvoice: (invoiceId: string) =>
    billingFetch<InvoiceItem>(`/invoices/${encodeURIComponent(invoiceId)}`),
  
  getReservationRecommendations: () =>
    billingFetch<{ items: ReservationRecommendation[] }>('/reservations/recommendations'),
  
  aiQuery: (req: AIQueryRequest) =>
    billingFetch<AIQueryResponse>('/ai/query', {
      method: 'POST',
      body: JSON.stringify(req)
    })
};
```

---

## 3. Component Specifications

All components live in `frontend/src/components/billing/`.

### Common conventions for all billing components:

```typescript
// All components follow this pattern:
const [data, setData] = useState<T | null>(null);
const [isLoading, setIsLoading] = useState(true);
const [error, setError] = useState<string | null>(null);

useEffect(() => {
  billingApi.getSomething()
    .then(setData)
    .catch(err => setError(err.message))
    .finally(() => setIsLoading(false));
}, []);

if (isLoading) return <div style={styles.loading}>Loading...</div>;
if (error) return <div style={styles.error}>Error: {error}</div>;
if (!data) return <div style={styles.empty}>No data available.</div>;
```

---

### 3.1 `BillingOverview.tsx`

**Props:** None (fetches its own data)

**Layout:** 2x2 grid of KPI stat cards + one wide trend preview chart

**KPI Cards:**
- "MTD Spend" — current month total cost, with MoM change arrow and percentage
- "Top Service" — highest-spending service this month with its cost
- "Budget Status" — highest utilized budget with progress bar (% used)
- "Cost Savings Available" — total potential monthly savings from active Advisor recommendations

**Colors:** Use `--color-accent-blue` for spend, `--color-accent-green` for savings, `--color-accent-red` for overbudget, `--color-accent-yellow` for warnings

**Icons (lucide-react):** `DollarSign`, `TrendingUp`, `TrendingDown`, `Target`, `AlertTriangle`, `CheckCircle`

---

### 3.2 `CostTrendChart.tsx`

**Props:**
```typescript
interface CostTrendChartProps {
  months?: number;           // default 12
  dimension?: string;        // default "ServiceName"
  title?: string;
}
```

**Chart:** Multi-series stacked bar chart using Recharts `BarChart` (the same library used by WidgetCard for bar charts — already a dependency, no new package needed).

**Behavior:**
- Shows the last N months on X-axis
- Y-axis in USD
- Each series is a different top-5 service (the rest are grouped as "Other")
- Hoverable tooltip shows cost breakdown
- Legend at bottom

---

### 3.3 `TopSpendersTable.tsx`

**Props:**
```typescript
interface TopSpendersTableProps {
  period?: string;           // YYYY-MM, default current month
  dimension?: string;        // default "ServiceName"
  limit?: number;            // default 15
}
```

**Layout:** Full-width table with columns:
- Rank (#)
- Service/RG Name
- Monthly Cost (formatted as $X,XXX.XX)
- % of Total (with narrow bar visualization)
- vs Prior Month (arrow + colored percentage: green for decrease, red for increase)
- Actions (no action buttons in V1 — placeholder column)

**Behavior:**
- Table rows are sorted by cost descending
- Clicking a row expands it to show the top 3 sub-resources within that service
- "% of Total" column shows a narrow inline progress bar

---

### 3.4 `BudgetCard.tsx`

**Props:**
```typescript
interface BudgetCardProps {
  budget: BudgetItem;
}
```

**Layout:** Card with:
- Budget name as header
- Scope (subscription/RG) as subheader
- Horizontal progress bar: filled portion = `utilization_pct`, color: green (<70%), yellow (70-90%), red (>90%), dark red (>100%)
- Current spend / Budget amount text below bar
- Time grain badge (Monthly, Quarterly, etc.)
- Forecast spend with arrow if available
- Alert icon if utilization > 80%

---

### 3.5 `AdvisorPanel.tsx`

**Props:** None (fetches its own data)

**Layout:**
- Summary header: "X recommendations · $Y,YYY.YY/month potential savings"
- Category filter tabs: All, Cost, Security, Performance, High Availability, Operational Excellence
- Impact filter: All, High, Medium, Low
- List of recommendation cards

**Recommendation card:**
- Impact badge (red = High, yellow = Medium, blue = Low)
- Category badge
- Resource name (impacted_value) + resource group
- Problem description (first 120 chars, expandable)
- Savings amount in green if available
- "View Details" expands to show full problem + solution text

---

### 3.6 `InvoiceList.tsx`

**Props:** None (fetches its own data)

**Layout:** Table with columns:
- Invoice ID
- Billing Period
- Invoice Date
- Amount (billed_amount, formatted as currency)
- Amount Due
- Status badge (green = Paid, blue = Due, red = PastDue, gray = Void)
- Download PDF link (opens invoice_download_url in new tab if available)

**Behavior:**
- Sorted by billing period descending (newest first)
- Status badges use appropriate colors matching existing dashboard badge style

---

### 3.7 `ReservationDashboard.tsx`

**Props:** None (fetches its own data)

**Layout:** Two sections:

**Section 1 — Purchase Opportunities:**
- Cards for each reservation recommendation
- Each card shows: SKU, region, recommended quantity, net monthly savings, payback period
- Cards sorted by net_savings descending
- "1-Year" vs "3-Year" tab filter

**Section 2 — (Future)** — Utilization details (V2 scope, show placeholder in V1)

---

### 3.8 `AICostAnalyst.tsx`

**Props:** None (manages own state)

**Layout:** Chat-style interface with:
- Message history area (scrollable)
- Input box at bottom with Send button
- Example question chips above the input box on empty state
- Source citations below AI responses (collapsible)

**Example question chips (shown when no messages):**
- "What are my top spending services this month?"
- "Where can I save the most money right now?"
- "Are any of my budgets at risk of being exceeded?"
- "What reserved instances should I purchase?"
- "Show me any unusual cost spikes this month."

**Message types:**
- User messages: right-aligned, accent background
- AI responses: left-aligned, card background, with sources section below
- Loading state: animated dots while waiting for response
- Error state: red error message with retry button

**Source citations:** Below each AI response, show a collapsed "Sources (N)" section. Expanding it shows a list of `AIQuerySource` items with document_type, period, dimension_value, and relevance score formatted as a percentage.

**State:**
```typescript
interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  sources?: AIQuerySource[];
  isLoading?: boolean;
  error?: string;
  timestamp: Date;
}

const [messages, setMessages] = useState<Message[]>([]);
const [inputValue, setInputValue] = useState('');
const [isQuerying, setIsQuerying] = useState(false);
```

---

## 4. Sidebar Modifications (`Sidebar.tsx`)

Add a new collapsible section below the existing dashboard list. The section header is "Azure Billing" with a `DollarSign` icon from `lucide-react`.

Navigation items (each calls a callback to set the active billing view):

```typescript
type BillingView = 
  | 'billing-overview'
  | 'billing-top-spenders'
  | 'billing-budgets'
  | 'billing-advisor'
  | 'billing-invoices'
  | 'billing-reservations'
  | 'billing-ai';

interface BillingSidebarItem {
  id: BillingView;
  label: string;
  icon: LucideIcon;
}

const BILLING_NAV_ITEMS: BillingSidebarItem[] = [
  { id: 'billing-overview',      label: 'Cost Overview',         icon: BarChart3 },
  { id: 'billing-top-spenders',  label: 'Top Spenders',          icon: TrendingUp },
  { id: 'billing-budgets',       label: 'Budgets & Alerts',       icon: Target },
  { id: 'billing-advisor',       label: 'Advisor',                icon: Lightbulb },
  { id: 'billing-invoices',      label: 'Invoices',               icon: FileText },
  { id: 'billing-reservations',  label: 'Reservations',           icon: Clock },
  { id: 'billing-ai',            label: 'AI Cost Analyst',        icon: Sparkles },
];
```

The sidebar section is collapsible with a chevron toggle, defaulting to expanded.

---

## 5. `App.tsx` Modifications

### State additions:
```typescript
const [activeBillingView, setActiveBillingView] = useState<BillingView | null>(null);
```

### Routing logic:
When `activeBillingView` is not null, render the corresponding billing component instead of the dashboard viewer:

```typescript
const renderMainContent = () => {
  if (activeBillingView) {
    switch (activeBillingView) {
      case 'billing-overview':     return <BillingOverview />;
      case 'billing-top-spenders': return <TopSpendersTable />;
      case 'billing-budgets':      return <div><BudgetCard ... /></div>;
      case 'billing-advisor':      return <AdvisorPanel />;
      case 'billing-invoices':     return <InvoiceList />;
      case 'billing-reservations': return <ReservationDashboard />;
      case 'billing-ai':           return <AICostAnalyst />;
      default: return null;
    }
  }
  // ... existing dashboard viewer rendering
};
```

When a dashboard is selected in the sidebar, set `activeBillingView` to `null` (and vice versa).

---

## 6. Frontend Testing (`__tests__/`)

New test files to create:

- `BillingOverview.test.tsx` — renders with mocked API responses, shows loading state, shows error state
- `AdvisorPanel.test.tsx` — renders recommendation cards, filters work
- `AICostAnalyst.test.tsx` — renders empty state with example chips, submits question, shows loading indicator
- `InvoiceList.test.tsx` — renders invoice table, status badges render correctly

All tests mock `billingApi` using `vi.mock('../services/billingApi')` (Vitest mock syntax).
