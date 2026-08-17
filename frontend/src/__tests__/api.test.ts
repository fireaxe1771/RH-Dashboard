import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  createApiFetch,
  setAuthToken,
  setMsalInstance,
  isTokenExpired,
  secondsUntilExpiry,
  refreshAccessToken,
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

  test('concurrent 401s are deduplicated to a single acquireTokenSilent', async () => {
    setMsalInstance(mockMsalInstance, mockAccount);
    // Both calls get 401 on first try, then 200 after refresh.
    (global.fetch as any)
      .mockResolvedValueOnce(jsonResponse({ detail: 'Unauthorized' }, { status: 401 }))
      .mockResolvedValueOnce(jsonResponse({ detail: 'Unauthorized' }, { status: 401 }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    // acquireTokenSilent resolves after a microtask; both 401 handlers should
    // share the same promise.
    mockAcquireTokenSilent.mockResolvedValueOnce({ idToken: 'refreshed-token' });

    const fetchWithAuth = createApiFetch('/api');
    const [result1, result2] = await Promise.all([
      fetchWithAuth<{ ok: boolean }>('/widgets'),
      fetchWithAuth<{ ok: boolean }>('/dashboards'),
    ]);

    expect(result1).toEqual({ ok: true });
    expect(result2).toEqual({ ok: true });
    // Only ONE silent refresh should have been triggered despite two 401s.
    expect(mockAcquireTokenSilent).toHaveBeenCalledTimes(1);
    expect(mockAcquireTokenRedirect).not.toHaveBeenCalled();
  });

  test('refresh failure triggers acquireTokenRedirect exactly once even with concurrent 401s', async () => {
    setMsalInstance(mockMsalInstance, mockAccount);
    (global.fetch as any)
      .mockResolvedValueOnce(jsonResponse({ detail: 'Unauthorized' }, { status: 401 }))
      .mockResolvedValueOnce(jsonResponse({ detail: 'Unauthorized' }, { status: 401 }));
    mockAcquireTokenSilent.mockRejectedValueOnce(new Error('silent refresh failed'));
    mockAcquireTokenRedirect.mockResolvedValueOnce(undefined);

    const fetchWithAuth = createApiFetch('/api');
    const results = await Promise.allSettled([
      fetchWithAuth('/widgets'),
      fetchWithAuth('/dashboards'),
    ]);

    // Both should reject (no retry after redirect-triggered failure).
    expect(results.every(r => r.status === 'rejected')).toBe(true);
    // Only ONE acquireTokenSilent and ONE acquireTokenRedirect despite two 401s.
    expect(mockAcquireTokenSilent).toHaveBeenCalledTimes(1);
    expect(mockAcquireTokenRedirect).toHaveBeenCalledTimes(1);
  });
});

describe('JWT expiry utilities', () => {
  // Helper: create a minimal JWT with a given exp (seconds from epoch).
  // Uses Base64url encoding (no padding) to match real Azure AD JWT format,
  // so the decoder's padding-restoration logic is exercised.
  function makeJwt(exp: number): string {
    const b64url = (str: string) =>
      btoa(str).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    const header = b64url(JSON.stringify({ alg: 'RS256', typ: 'JWT' }));
    const payload = b64url(JSON.stringify({ exp, aud: 'test-client' }));
    return `${header}.${payload}.fake-signature`;
  }

  test('isTokenExpired returns true for an expired token', () => {
    const expiredToken = makeJwt(Math.floor(Date.now() / 1000) - 60);
    expect(isTokenExpired(expiredToken)).toBe(true);
  });

  test('isTokenExpired returns false for a valid token', () => {
    const validToken = makeJwt(Math.floor(Date.now() / 1000) + 3600);
    expect(isTokenExpired(validToken)).toBe(false);
  });

  test('isTokenExpired respects leeway', () => {
    const almostExpired = makeJwt(Math.floor(Date.now() / 1000) + 30);
    // Without leeway: not expired yet.
    expect(isTokenExpired(almostExpired)).toBe(false);
    // With 60s leeway: considered expired.
    expect(isTokenExpired(almostExpired, 60)).toBe(true);
  });

  test('isTokenExpired returns true for unparseable token', () => {
    expect(isTokenExpired('not-a-jwt')).toBe(true);
    expect(isTokenExpired('')).toBe(true);
  });

  test('secondsUntilExpiry returns correct remaining time', () => {
    const inOneHour = Math.floor(Date.now() / 1000) + 3600;
    const token = makeJwt(inOneHour);
    const remaining = secondsUntilExpiry(token);
    expect(remaining).toBeGreaterThan(3590);
    expect(remaining).toBeLessThanOrEqual(3600);
  });

  test('secondsUntilExpiry returns 0 for expired token', () => {
    const token = makeJwt(Math.floor(Date.now() / 1000) - 60);
    expect(secondsUntilExpiry(token)).toBe(0);
  });

  test('secondsUntilExpiry returns 0 for unparseable token', () => {
    expect(secondsUntilExpiry('garbage')).toBe(0);
  });

  test('decoder handles Base64url payloads that require padding restoration', () => {
    // Craft a payload whose Base64url encoding is NOT a multiple of 4,
    // so it requires '=' padding before atob() can decode it.
    // {"exp":1700000000,"x":"a"} is 26 bytes → 36 base64 chars (with 1 '='
    // pad) → 35 base64url chars (mod 4 = 3, requires padding restoration).
    const exp = Math.floor(Date.now() / 1000) + 3600;
    const payloadJson = JSON.stringify({ exp, x: 'a' });
    const b64url = (str: string) =>
      btoa(str).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    const header = b64url(JSON.stringify({ alg: 'RS256', typ: 'JWT' }));
    const payload = b64url(payloadJson);
    // Verify the payload length is not a multiple of 4 (exercises padding).
    expect(payload.length % 4).not.toBe(0);
    const token = `${header}.${payload}.fake-signature`;

    // If padding restoration is broken, this returns 0 (decode failed).
    expect(secondsUntilExpiry(token)).toBeGreaterThan(0);
    expect(isTokenExpired(token)).toBe(false);
  });
});

describe('refreshAccessToken direct usage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setAuthToken(null);
    setMsalInstance(null, null);
  });

  afterEach(() => {
    setAuthToken(null);
    setMsalInstance(null, null);
  });

  test('returns null when no MSAL instance is set', async () => {
    const result = await refreshAccessToken();
    expect(result).toBeNull();
  });

  test('returns the refreshed idToken and updates activeToken', async () => {
    setMsalInstance(mockMsalInstance, mockAccount);
    mockAcquireTokenSilent.mockResolvedValueOnce({ idToken: 'new-id-token' });

    const result = await refreshAccessToken();
    expect(result).toBe('new-id-token');
    // Verify the shared token was updated.
    const { getAuthToken } = await import('../services/fetchWrapper');
    expect(getAuthToken()).toBe('new-id-token');
  });
});
