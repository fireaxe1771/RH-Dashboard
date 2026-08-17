import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, waitFor } from '@testing-library/react';
import React from 'react';
import { AuthProvider, useAuth } from '../components/AuthContext';

// Mock MSAL React hooks
const mockLoginPopup = vi.fn().mockResolvedValue(undefined);
const mockLoginRedirect = vi.fn().mockResolvedValue(undefined);
const mockLogoutRedirect = vi.fn().mockResolvedValue(undefined);
const mockAcquireTokenSilent = vi.fn().mockResolvedValue({ accessToken: 'test-token-123', idToken: 'test-id-token-456' });
const mockAcquireTokenRedirect = vi.fn();
const mockHandleRedirectPromise = vi.fn().mockResolvedValue(null);

vi.mock('@azure/msal-react', () => ({
  useMsal: () => ({
    instance: {
      loginPopup: mockLoginPopup,
      loginRedirect: mockLoginRedirect,
      logoutRedirect: mockLogoutRedirect,
      acquireTokenSilent: mockAcquireTokenSilent,
      acquireTokenRedirect: mockAcquireTokenRedirect,
      handleRedirectPromise: mockHandleRedirectPromise,
    },
    accounts: [] as any[],
    inProgress: 'none',
  }),
  useIsAuthenticated: () => false,
}));

vi.mock('../services/api', () => ({
  setAuthToken: vi.fn(),
  setMsalInstance: vi.fn(),
  getAuthToken: vi.fn(() => null),
  refreshAccessToken: vi.fn(() => Promise.resolve(null)),
  secondsUntilExpiry: vi.fn(() => 3600),
}));

import { setAuthToken } from '../services/api';

// Consumer component that exposes auth context values
function AuthConsumer() {
  const auth = useAuth();
  return (
    <div>
      <span data-testid="is-authenticated">{String(auth.isAuthenticated)}</span>
      <span data-testid="loading">{String(auth.loading)}</span>
      <span data-testid="user-name">{auth.user?.name || 'none'}</span>
      <span data-testid="user-email">{auth.user?.email || 'none'}</span>
      <button data-testid="login-btn" onClick={() => auth.login()}>Login</button>
      <button data-testid="logout-btn" onClick={() => auth.logout()}>Logout</button>
    </div>
  );
}

describe('AuthContext', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  test('useAuth throws when used outside AuthProvider', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const BadConsumer = () => {
      useAuth();
      return null;
    };
    expect(() => render(<BadConsumer />)).toThrow('useAuth must be used within an AuthProvider');
    spy.mockRestore();
  });

  test('shows fatal configuration error when VITE_AZURE_CLIENT_ID is missing', async () => {
    vi.stubEnv('VITE_AZURE_CLIENT_ID', '');
    vi.stubEnv('VITE_AZURE_TENANT_ID', 'test-tenant');
    vi.stubEnv('VITE_DEV_AUTH_BYPASS', 'false');

    render(
      <AuthProvider>
        <div>child</div>
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Fatal Configuration Error/i)).toBeInTheDocument());
    expect(screen.getByText(/VITE_AZURE_CLIENT_ID is not configured/i)).toBeInTheDocument();
  });

  test('shows fatal configuration error when VITE_AZURE_TENANT_ID is missing', async () => {
    vi.stubEnv('VITE_AZURE_CLIENT_ID', 'test-client-id');
    vi.stubEnv('VITE_AZURE_TENANT_ID', '');
    vi.stubEnv('VITE_DEV_AUTH_BYPASS', 'false');

    render(
      <AuthProvider>
        <div>child</div>
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Fatal Configuration Error/i)).toBeInTheDocument());
    expect(screen.getByText(/VITE_AZURE_TENANT_ID is not configured/i)).toBeInTheDocument();
  });

  test('provides dev bypass user when VITE_DEV_AUTH_BYPASS is true', async () => {
    vi.stubEnv('VITE_DEV_AUTH_BYPASS', 'true');
    vi.stubEnv('VITE_AZURE_CLIENT_ID', 'test-client');
    vi.stubEnv('VITE_AZURE_TENANT_ID', 'test-tenant');

    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>,
    );

    expect(screen.getByTestId('is-authenticated').textContent).toBe('true');
    expect(screen.getByTestId('loading').textContent).toBe('false');
    expect(screen.getByTestId('user-name').textContent).toBe('Local Dev User');
    expect(screen.getByTestId('user-email').textContent).toBe('dev.local@streamlineas.com');
    expect(setAuthToken).toHaveBeenCalledWith(null);
  });

  test('login is a no-op in dev bypass mode', async () => {
    vi.stubEnv('VITE_DEV_AUTH_BYPASS', 'true');
    vi.stubEnv('VITE_AZURE_CLIENT_ID', 'test-client');
    vi.stubEnv('VITE_AZURE_TENANT_ID', 'test-tenant');

    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>,
    );
    await act(async () => {
      screen.getByTestId('login-btn').click();
    });
    expect(mockLoginRedirect).not.toHaveBeenCalled();
  });

  test('logout is a no-op in dev bypass mode', async () => {
    vi.stubEnv('VITE_DEV_AUTH_BYPASS', 'true');
    vi.stubEnv('VITE_AZURE_CLIENT_ID', 'test-client');
    vi.stubEnv('VITE_AZURE_TENANT_ID', 'test-tenant');

    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>,
    );
    await act(async () => {
      screen.getByTestId('logout-btn').click();
    });
    expect(mockLogoutRedirect).not.toHaveBeenCalled();
  });

  test('renders children when properly configured', () => {
    vi.stubEnv('VITE_AZURE_CLIENT_ID', 'test-client-id');
    vi.stubEnv('VITE_AZURE_TENANT_ID', 'test-tenant-id');
    vi.stubEnv('VITE_DEV_AUTH_BYPASS', 'false');

    render(
      <AuthProvider>
        <div data-testid="child-content">Hello World</div>
      </AuthProvider>,
    );

    expect(screen.getByTestId('child-content')).toBeInTheDocument();
  });
});
