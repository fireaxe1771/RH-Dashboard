/**
 * E2E tests for the Azure Billing Invoices view.
 */
import { test, expect } from './fixtures';

test.describe('Billing: Invoices', () => {
  test('displays a list of invoices', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Invoices' }).click();

    await expect(page.locator('.navbar-title')).toHaveText('Invoices', { timeout: 10000 });

    // Invoice rows
    await expect(page.getByText('inv-001')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('inv-002')).toBeVisible();
  });

  test('shows invoice status badges', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Invoices' }).click();

    await expect(page.getByText('Paid').first()).toBeVisible({ timeout: 10000 });
  });

  test('shows empty state when no invoices', async ({ page, mockApi }) => {
    await mockApi({ invoices: [] });
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Invoices' }).click();

    await expect(page.getByText(/No invoices/i)).toBeVisible({ timeout: 10000 });
  });
});
