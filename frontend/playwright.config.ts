import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright E2E test configuration for the RecoveryHub Dashboard frontend.
 *
 * Tests run against the Vite dev server with DEV_AUTH_BYPASS=true so no
 * real Azure AD authentication is required. All /api/* calls are intercepted
 * and mocked by the test fixtures — no backend or database is needed.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    baseURL: 'http://localhost:3001',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:3001',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    env: {
      VITE_DEV_AUTH_BYPASS: 'true',
      VITE_AZURE_CLIENT_ID: 'test-client-id',
      VITE_AZURE_TENANT_ID: 'test-tenant-id',
    },
  },
});
