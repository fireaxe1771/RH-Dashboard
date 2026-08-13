import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { aiAnalyticsApi, AiAnalyticsFilters } from '../services/aiAnalyticsApi';

// Mock fetch so we can capture the URLs that each API method constructs.
// The fetchWrapper handles auth/refresh — here we only verify that the
// correct URL path + query string is built from the filters.

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('aiAnalyticsApi URL construction', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.clearAllMocks();
    global.fetch = vi.fn() as unknown as typeof fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  function lastUrl(): string {
    const calls = (global.fetch as any).mock.calls;
    return calls[calls.length - 1][0] as string;
  }

  test('getOutcomeSummary builds query from filters', async () => {
    (global.fetch as any).mockResolvedValueOnce(jsonResponse({}));
    const filters: AiAnalyticsFilters = {
      start_date: '2026-01-01',
      end_date: '2026-01-31',
      department_id: 5,
      business_outcome: 'released',
    };
    await aiAnalyticsApi.getOutcomeSummary(filters);
    const url = lastUrl();
    expect(url).toContain('/outcomes/summary');
    expect(url).toContain('start_date=2026-01-01');
    expect(url).toContain('end_date=2026-01-31');
    expect(url).toContain('department_id=5');
    expect(url).toContain('business_outcome=released');
  });

  test('getOutcomeTrend appends grain parameter', async () => {
    (global.fetch as any).mockResolvedValueOnce(jsonResponse([]));
    await aiAnalyticsApi.getOutcomeTrend({ start_date: '2026-01-01' }, 'week');
    const url = lastUrl();
    expect(url).toContain('/outcomes/trend');
    expect(url).toContain('grain=week');
  });

  test('getOutcomeTrend defaults to day grain', async () => {
    (global.fetch as any).mockResolvedValueOnce(jsonResponse([]));
    await aiAnalyticsApi.getOutcomeTrend({});
    const url = lastUrl();
    expect(url).toContain('grain=day');
  });

  test('empty filters produce minimal query string', async () => {
    (global.fetch as any).mockResolvedValueOnce(jsonResponse({}));
    await aiAnalyticsApi.getOutcomeSummary({});
    const url = lastUrl();
    expect(url).toContain('/outcomes/summary');
    // No filter params should be present — only the empty query string
    expect(url).not.toContain('start_date=');
    expect(url).not.toContain('department_id=');
  });

  test('confidence_min and confidence_max use undefined check (0 is valid)', async () => {
    (global.fetch as any).mockResolvedValueOnce(jsonResponse({}));
    await aiAnalyticsApi.getOutcomeSummary({ confidence_min: 0, confidence_max: 100 });
    const url = lastUrl();
    // confidence_min=0 should be included (undefined check, not truthy check)
    expect(url).toContain('confidence_min=0');
    expect(url).toContain('confidence_max=100');
  });

  test('has_retry=false is included (undefined check, not truthy)', async () => {
    (global.fetch as any).mockResolvedValueOnce(jsonResponse({}));
    await aiAnalyticsApi.getOutcomeSummary({ has_retry: false });
    const url = lastUrl();
    expect(url).toContain('has_retry=false');
  });

  test('getInvoiceCohort passes page and page_size', async () => {
    (global.fetch as any).mockResolvedValueOnce(jsonResponse({ invoices: [], total_count: 0, page: 1, page_size: 50, source_status: {}, data_complete: true }));
    await aiAnalyticsApi.getInvoiceCohort({ page: 2, page_size: 100 });
    const url = lastUrl();
    expect(url).toContain('page=2');
    expect(url).toContain('page_size=100');
  });

  test('getInvoiceTrace builds path with claimId', async () => {
    (global.fetch as any).mockResolvedValueOnce(jsonResponse({}));
    await aiAnalyticsApi.getInvoiceTrace(42);
    const url = lastUrl();
    expect(url).toContain('/invoices/42/trace');
  });

  test('all filter params are included when provided', async () => {
    (global.fetch as any).mockResolvedValueOnce(jsonResponse({}));
    const filters: AiAnalyticsFilters = {
      start_date: '2026-01-01',
      end_date: '2026-01-31',
      department_id: 5,
      business_outcome: 'released',
      ai_processing_status: 'completed',
      agent_execution_status: 'success',
      confidence_min: 50,
      confidence_max: 95,
      has_retry: true,
      writeback_status: 'success',
      billing_category: 'billable',
      reason_category: 'documentation',
      page: 1,
      page_size: 50,
      sort_by: 'claim_id',
      sort_direction: 'desc',
      date_basis: 'ai_business_updated_at',
    };
    await aiAnalyticsApi.getOutcomeSummary(filters);
    const url = lastUrl();
    expect(url).toContain('ai_processing_status=completed');
    expect(url).toContain('agent_execution_status=success');
    expect(url).toContain('writeback_status=success');
    expect(url).toContain('billing_category=billable');
    expect(url).toContain('reason_category=documentation');
    expect(url).toContain('sort_by=claim_id');
    expect(url).toContain('sort_direction=desc');
    expect(url).toContain('date_basis=ai_business_updated_at');
  });
});
