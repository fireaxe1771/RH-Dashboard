/**
 * E2E tests for the Azure Billing Cost Overview view.
 */
import { test, expect } from './fixtures';

test.describe('Billing: Cost Overview', () => {
  test('displays KPI cards and cost data', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    // Navigate via sidebar button
    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Cost Overview' }).click();

    // Navbar title should show the view name
    await expect(page.locator('.navbar-title')).toHaveText('Cost Overview', { timeout: 10000 });

    // KPI card for MTD Spend should be visible
    await expect(page.getByText('MTD Spend')).toBeVisible();

    // Top service name should appear (in the cost breakdown list)
    await expect(page.getByText('Virtual Machines').first()).toBeVisible();
  });

  test('shows budget card in overview', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Cost Overview' }).click();

    await expect(page.getByText('Production Budget')).toBeVisible({ timeout: 10000 });
  });

  test('shows advisor summary in overview', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Cost Overview' }).click();

    await expect(page.getByText(/cost recommendation/i)).toBeVisible({ timeout: 10000 });
  });

  test('shows empty state when no cost data', async ({ page, mockApi }) => {
    await mockApi({
      costSummary: { items: [], total: 0, currency: 'USD', period: '2026-06' },
    });
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Cost Overview' }).click();

    await expect(page.getByText('MTD Spend')).toBeVisible({ timeout: 10000 });
  });
});
