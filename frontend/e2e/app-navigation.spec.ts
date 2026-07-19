/**
 * E2E tests for the main application shell: sign-in screen, sidebar navigation,
 * and dashboard list/viewer.
 */
import { test, expect } from './fixtures';

test.describe('Application navigation', () => {
  test('loads the app and shows the sidebar with dashboard list', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    await expect(page.getByTestId('sidebar')).toBeVisible();
    await expect(page.locator('.sidebar-logo')).toHaveText('RecoveryHub');

    // The seeded dashboard should appear in the sidebar
    const sidebar = page.getByTestId('sidebar');
    await expect(sidebar.getByText('Fire Claims Overview')).toBeVisible();
  });

  test('shows New Dashboard button in sidebar', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await expect(sidebar.getByRole('button', { name: /New Dashboard/i })).toBeVisible();
  });

  test('clicking a dashboard shows the viewer with widgets', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    // Click on the dashboard in the sidebar
    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByText('Fire Claims Overview').click();

    // The dashboard viewer should render with widget titles
    await expect(page.getByTestId('dashboard-viewer')).toBeVisible({ timeout: 10000 });
  });

  test('Azure Billing section is visible in sidebar', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await expect(sidebar.getByText('Azure Billing')).toBeVisible();
  });

  test('billing nav items are visible when expanded', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await expect(sidebar.getByRole('button', { name: 'Cost Overview' })).toBeVisible();
    await expect(sidebar.getByRole('button', { name: 'Top Spenders' })).toBeVisible();
    await expect(sidebar.getByRole('button', { name: 'Budgets & Alerts' })).toBeVisible();
    await expect(sidebar.getByRole('button', { name: 'Advisor' })).toBeVisible();
    await expect(sidebar.getByRole('button', { name: 'Invoices' })).toBeVisible();
    await expect(sidebar.getByRole('button', { name: 'Reservations' })).toBeVisible();
    await expect(sidebar.getByRole('button', { name: 'AI Cost Analyst' })).toBeVisible();
  });

  test('collapsing billing section hides nav items', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    // Click the Azure Billing header to collapse
    await sidebar.getByText('Azure Billing').click();

    await expect(sidebar.getByRole('button', { name: 'Cost Overview' })).toBeHidden();
    await expect(sidebar.getByRole('button', { name: 'Top Spenders' })).toBeHidden();
  });

  test('user profile shows dev user info', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    await expect(page.getByText('Local Dev User')).toBeVisible();
    await expect(page.getByText('dev.local@streamlineas.com')).toBeVisible();
  });

  test('New Dashboard button opens the designer', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: /New Dashboard/i }).click();

    // Designer should be visible
    await expect(page.getByText(/Dashboard Name/i)).toBeVisible({ timeout: 10000 });
  });
});
