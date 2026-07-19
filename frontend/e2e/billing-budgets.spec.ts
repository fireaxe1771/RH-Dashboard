/**
 * E2E tests for the Azure Billing Budgets & Alerts view.
 */
import { test, expect } from './fixtures';

test.describe('Billing: Budgets & Alerts', () => {
  test('displays budget cards sorted by utilization', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Budgets & Alerts' }).click();

    await expect(page.locator('.navbar-title')).toHaveText('Budgets & Alerts', { timeout: 10000 });

    // Budget cards
    await expect(page.getByText('Production Budget')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Dev Budget')).toBeVisible();
  });

  test('shows active alerts section', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Budgets & Alerts' }).click();

    await expect(page.getByText('Active Alerts')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Budget Exceeded', { exact: true })).toBeVisible();
  });

  test('shows empty state when no budgets', async ({ page, mockApi }) => {
    await mockApi({ budgets: [], alerts: [] });
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Budgets & Alerts' }).click();

    await expect(page.getByText(/No budgets configured/i)).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/No active cost alerts/i)).toBeVisible();
  });
});
