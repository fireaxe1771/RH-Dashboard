/**
 * E2E tests for the "Deleted Drafts YTD" stat widget on the Claims dashboard.
 *
 * The widget reads from the `deleted_claims` table and filters on both the
 * draft `created` date and the deletion `timestamp` falling within the
 * year-to-date window. These tests verify the widget renders correctly in the
 * dashboard viewer when the backend returns a typical formatted stat result
 * (e.g. "12 / 1 per week"). All API calls are mocked via the shared fixtures —
 * no backend or SQL Server is required.
 */
import { test, expect } from './fixtures';

// A Claims dashboard seeded with the Deleted Drafts YTD stat widget, mirroring
// the widget definition produced by _build_default_claims_dashboard() in the
// backend.
const CLAIMS_DASHBOARD_WITH_DELETED_DRAFTS = [
  {
    _id: 'dash-claims',
    id: 'dash-claims',
    name: 'Claims Breakdown',
    description: 'Year-to-date claims dashboard',
    created_by: 'dev.local@streamlineas.com',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    widgets: [
      {
        id: 'claims-draft-intake-ytd',
        title: 'Drafts Created YTD',
        type: 'stat',
        sql_query: 'SELECT 1 AS Count',
        layout: { x: 0, y: 0, w: 3, h: 3 },
        config: { xAxisKey: '', yAxisKeys: [], colors: ['#6366f1'] },
      },
      {
        id: 'claims-draft-deleted-ytd',
        title: 'Deleted Drafts YTD',
        type: 'stat',
        sql_query: 'SELECT 1 AS Count',
        layout: { x: 3, y: 0, w: 3, h: 3 },
        config: { xAxisKey: '', yAxisKeys: [], colors: ['#ef4444'] },
      },
    ],
  },
];

// Mirrors the backend's FORMAT(...) output: a single string column "Count"
// shaped as "N / X per week".
const DELETED_DRAFTS_RESULT = {
  columns: ['Count'],
  rows: [{ Count: '12 / 1 per week' }],
};

test.describe('Claims dashboard: Deleted Drafts YTD widget', () => {
  test('renders the Deleted Drafts YTD stat card with a formatted value', async ({ page, mockApi }) => {
    await mockApi({
      dashboards: CLAIMS_DASHBOARD_WITH_DELETED_DRAFTS,
      queryResult: DELETED_DRAFTS_RESULT,
    });
    await page.goto('/');

    // Open the Claims dashboard from the sidebar.
    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByText('Claims Breakdown').click();

    // Wait for the dashboard viewer to mount its widget grid.
    const viewer = page.getByTestId('dashboard-viewer');
    await expect(viewer).toBeVisible({ timeout: 10000 });

    // The Deleted Drafts YTD stat card title should be visible.
    const deletedDraftsCard = viewer.locator('.stat-card', { hasText: 'Deleted Drafts YTD' });
    await expect(deletedDraftsCard).toBeVisible({ timeout: 10000 });
    await expect(deletedDraftsCard.getByText('Deleted Drafts YTD')).toBeVisible();

    // The formatted stat value returned by the mocked query should render.
    await expect(deletedDraftsCard.locator('.stat-value')).toHaveText('12 / 1 per week');
  });

  test('shows both Drafts Created YTD and Deleted Drafts YTD cards together', async ({ page, mockApi }) => {
    await mockApi({
      dashboards: CLAIMS_DASHBOARD_WITH_DELETED_DRAFTS,
      queryResult: DELETED_DRAFTS_RESULT,
    });
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByText('Claims Breakdown').click();

    const viewer = page.getByTestId('dashboard-viewer');
    await expect(viewer).toBeVisible({ timeout: 10000 });

    // Both YTD stat cards should render side by side in the viewer.
    await expect(viewer.locator('.stat-card', { hasText: 'Drafts Created YTD' })).toBeVisible({ timeout: 10000 });
    await expect(viewer.locator('.stat-card', { hasText: 'Deleted Drafts YTD' })).toBeVisible();
  });
});
