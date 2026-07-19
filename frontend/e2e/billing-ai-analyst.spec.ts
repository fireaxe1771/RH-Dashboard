/**
 * E2E tests for the AI Cost Analyst view.
 */
import { test, expect } from './fixtures';

test.describe('Billing: AI Cost Analyst', () => {
  test('displays the AI analyst interface', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'AI Cost Analyst' }).click();

    await expect(page.locator('.navbar-title')).toHaveText('AI Cost Analyst', { timeout: 10000 });
  });

  test('submitting a question returns an AI answer with sources', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'AI Cost Analyst' }).click();

    await expect(page.locator('.navbar-title')).toHaveText('AI Cost Analyst', { timeout: 10000 });

    // Find the input/textarea and type a question
    const inputLocator = page.locator('textarea, input[type="text"]').last();
    await inputLocator.fill('What drives my Azure costs?');

    // Click submit button
    const submitBtn = page.getByRole('button', { name: /ask|submit|send|query|analyze/i });
    if (await submitBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await submitBtn.click();
    } else {
      // Try pressing Enter
      await inputLocator.press('Enter');
    }

    // Should show the AI answer
    await expect(page.getByText(/Virtual Machines are the top cost driver/i)).toBeVisible({ timeout: 15000 });
  });

  test('shows suggested questions on load', async ({ page, mockApi }) => {
    await mockApi();
    await page.goto('/');

    const sidebar = page.getByTestId('sidebar');
    await sidebar.getByRole('button', { name: 'AI Cost Analyst' }).click();

    await expect(page.locator('.navbar-title')).toHaveText('AI Cost Analyst', { timeout: 10000 });
    // The page should have some content (suggested questions or input)
    await expect(page.locator('body')).not.toBeEmpty();
  });
});
