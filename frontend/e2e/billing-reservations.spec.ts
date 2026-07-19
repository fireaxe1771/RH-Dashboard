/**
 * E2E tests for the Azure Billing Reservations view.
 */
import { test, expect } from './fixtures';

test.describe('Billing: Reservations', () => {
  test('displays reservation recommendations with term tabs', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Reservations' }).click();

    await expect(page.locator('.navbar-title')).toHaveText('Reservations', { timeout: 10000 });

    // Term tabs
    await expect(page.getByRole('button', { name: '1-Year' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('button', { name: '3-Year' })).toBeVisible();
  });

  test('shows P1Y recommendations by default', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Reservations' }).click();

    await expect(page.getByText('Standard_D2s_v5')).toBeVisible({ timeout: 10000 });
  });

  test('switching to 3-Year tab shows P3Y recommendations', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Reservations' }).click();

    await expect(page.getByText('Standard_D2s_v5')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: '3-Year' }).click();
    await expect(page.getByText('Standard_F4s_v5')).toBeVisible({ timeout: 5000 });
  });

  test('shows net savings and payback period', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Reservations' }).click();

    await expect(page.getByText('Net savings')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/Payback/i)).toBeVisible();
  });

  test('shows empty state when no recommendations', async ({ page, mockApi }) => {
    await mockApi({ reservations: [] });
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Reservations' }).click();

    await expect(page.getByText(/No 1-Year reservation/i)).toBeVisible({ timeout: 10000 });
  });
});
