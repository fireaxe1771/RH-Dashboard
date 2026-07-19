/**
 * E2E tests for the Azure Billing Top Spenders view.
 */
import { test, expect } from './fixtures';

test.describe('Billing: Top Spenders', () => {
  test('displays a table of top spending services', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Top Spenders' }).click();

    // Navbar title
    await expect(page.locator('.navbar-title')).toHaveText('Top Spenders', { timeout: 10000 });

    // Table header columns
    await expect(page.getByText('Monthly Cost')).toBeVisible();

    // Data rows
    await expect(page.getByText('Virtual Machines')).toBeVisible();
    await expect(page.getByText('Storage')).toBeVisible();
    await expect(page.getByText('Network')).toBeVisible();
  });

  test('shows rank numbers for each row', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Top Spenders' }).click();

    await expect(page.locator('td').first()).toContainText('1', { timeout: 10000 });
  });

  test('shows empty state when no data', async ({ page, mockApi }) => {
    await mockApi({ topSpenders: [] });
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Top Spenders' }).click();

    await expect(page.getByText(/No spending data/i)).toBeVisible({ timeout: 10000 });
  });
});
