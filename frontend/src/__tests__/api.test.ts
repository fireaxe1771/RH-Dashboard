import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  createApiFetch,
  setAuthToken,
  setMsalInstance,
} from '../services/fetchWrapper';

// Mock MSAL instance used by the fetch wrapper for silent refresh + redirect.
const mockAcquireTokenSilent = vi.fn();
const mockAcquireTokenRedirect = vi.fn();
const mockMsalInstance: any = {
  acquireTokenSilent: mockAcquireTokenSilent,
  acquireTokenRedirect: mockAcquireTokenRedirect,
};
const mockAccount: any = { username: 'test@streamlineas.com' };

function jsonResponse(body: unknown, init?: { status?: number; statusText?: string }): Response {
  const status = init?.status ?? 200;
  return new Response(JSON.stringify(body), {
    status,
    statusText: init?.statusText ?? (status === 200 ? 'OK' : 'Error'),
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('fetchWrapper / api 401-refresh-retry', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.clearAllMocks();
    setAuthToken(null);
    setMsalInstance(null, null);
    global.fetch = vi.fn() as unknown as typeof fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    setAuthToken(null);
    setMsalInstance(null, null);
  });

  test('successful request on first try (no refresh needed)', async () => {
    (global.fetch as any).mockResolvedValueOnce(jsonResponse({ ok: true }));
    const fetchWithAuth = createApiFetch('/api');
    const result = await fetchWithAuth<{ ok: boolean }>('/widgets');
    expect(result).toEqual({ ok: true });
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(mockAcquireTokenSilent).not.toHaveBeenCalled();
  });

  test('401 on first try -> silent refresh succeeds -> retry succeeds', async () => {
    setMsalInstance(mockMsalInstance, mockAccount);
    (global.fetch as any)
      .mockResolvedValueOnce(jsonResponse({ detail: 'Unauthorized' }, { status: 401 }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    mockAcquireTokenSilent.mockResolvedValueOnce({ idToken: 'refreshed-token' });

    const fetchWithAuth = createApiFetch('/api');
    const result = await fetchWithAuth<{ ok: boolean }>('/widgets');

    expect(result).toEqual({ ok: true });
    expect(global.fetch).toHaveBeenCalledTimes(2);
    expect(mockAcquireTokenSilent).toHaveBeenCalledTimes(1);
    // The retried request should carry the refreshed bearer token.
    const retryCall = (global.fetch as any).mock.calls[1][1] as RequestInit;
    expect((retryCall.headers as Record<string, string>)['Authorization']).toBe(
      'Bearer refreshed-token',
    );
  });

  test('401 on first try -> silent refresh fails -> acquireTokenRedirect is called', async () => {
    setMsalInstance(mockMsalInstance, mockAccount);
    (global.fetch as any).mockResolvedValueOnce(
      jsonResponse({ detail: 'Unauthorized' }, { status: 401 }),
    );
    mockAcquireTokenSilent.mockRejectedValueOnce(new Error('silent refresh failed'));
    mockAcquireTokenRedirect.mockResolvedValueOnce(undefined);

    const fetchWithAuth = createApiFetch('/api');
    // The wrapper returns null token on refresh failure and propagates the 401
    // as an error (the redirect is triggered as a side effect).
    await expect(fetchWithAuth('/widgets')).rejects.toThrow(/Unauthorized/);

    expect(mockAcquireTokenSilent).toHaveBeenCalledTimes(1);
    expect(mockAcquireTokenRedirect).toHaveBeenCalledTimes(1);
    // No retry fetch is performed when refresh fails.
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  test('non-401 error -> error message parsed from detail field', async () => {
    (global.fetch as any).mockResolvedValueOnce(
      jsonResponse({ detail: 'Something went wrong' }, { status: 500 }),
    );
    const fetchWithAuth = createApiFetch('/api');
    await expect(fetchWithAuth('/widgets')).rejects.toThrow('Something went wrong');
    expect(mockAcquireTokenSilent).not.toHaveBeenCalled();
  });

  test('headers include Authorization: Bearer <token> when token is set', async () => {
    setAuthToken('my-token');
    (global.fetch as any).mockResolvedValueOnce(jsonResponse({ ok: true }));
    const fetchWithAuth = createApiFetch('/api');
    await fetchWithAuth('/widgets');
    const call = (global.fetch as any).mock.calls[0];
    const options = call[1] as RequestInit;
    expect((options.headers as Record<string, string>)['Authorization']).toBe(
      'Bearer my-token',
    );
  });

  test('headers omit Authorization when no token is set', async () => {
    (global.fetch as any).mockResolvedValueOnce(jsonResponse({ ok: true }));
    const fetchWithAuth = createApiFetch('/api');
    await fetchWithAuth('/widgets');
    const call = (global.fetch as any).mock.calls[0];
    const options = call[1] as RequestInit;
    const headers = options.headers as Record<string, string>;
    expect(headers['Authorization']).toBeUndefined();
    expect(headers['Content-Type']).toBe('application/json');
  });
});
