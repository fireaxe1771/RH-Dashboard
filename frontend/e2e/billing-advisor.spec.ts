/**
 * E2E tests for the Azure Advisor view.
 */
import { test, expect } from './fixtures';

test.describe('Billing: Azure Advisor', () => {
  test('displays advisor summary and recommendation cards', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Advisor' }).click();

    await expect(page.locator('.navbar-title')).toHaveText('Azure Advisor', { timeout: 10000 });

    // Recommendation cards should appear
    await expect(page.getByText('vm-prod-01')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('storage-acc-01')).toBeVisible();
  });

  test('shows potential savings amount', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Advisor' }).click();

    await expect(page.getByText(/potential savings/i)).toBeVisible({ timeout: 10000 });
  });

  test('category filter buttons are clickable', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Advisor' }).click();

    // Wait for recommendations to load
    await expect(page.getByText('vm-prod-01')).toBeVisible({ timeout: 10000 });

    // Category filter buttons — use role to disambiguate from other "Cost" text
    const filterButtons = page.locator('button', { hasText: 'Security' });
    await filterButtons.first().click();
  });

  test('View Details expands solution text', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'Advisor' }).click();

    await expect(page.getByText('vm-prod-01')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /View Details/i }).first().click();
    await expect(page.getByText(/Recommended action/i)).toBeVisible({ timeout: 5000 });
  });
});
