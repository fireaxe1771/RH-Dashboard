/**
 * Shared fetch wrapper with auth-header injection, 401 silent-refresh-retry,
 * and consistent error parsing. Used by all API service clients
 * (api.ts, billingApi.ts, aiAnalyticsApi.ts, aiAdoptionApi.ts) so that
 * expired-token handling is identical across every endpoint group.
 */
import { AccountInfo, IPublicClientApplication } from '@azure/msal-browser';
import { loginRequest } from '../authConfig';

let activeToken: string | null = null;

// Reference to the MSAL instance, set by setMsalInstance(). Used to
// silently refresh expired tokens when the API returns 401.
let msalInstance: IPublicClientApplication | null = null;
let msalAccount: AccountInfo | null = null;

// Deduplicated refresh state: when multiple API calls get 401 simultaneously,
// they all share a single acquireTokenSilent promise instead of each firing
// their own (which causes redirect storms when the silent refresh fails).
let refreshPromise: Promise<string | null> | null = null;

// Guard against multiple acquireTokenRedirect calls. Once a redirect is
// triggered, the page will navigate away; this flag prevents concurrent 401
// handlers from each calling acquireTokenRedirect before the navigation happens.
let isRedirecting = false;

/**
 * Sets the active OAuth bearer token shared by all API service clients.
 */
export function setAuthToken(token: string | null): void {
  activeToken = token;
}

/**
 * Returns the active OAuth bearer token (shared with the billing/AI API clients).
 */
export function getAuthToken(): string | null {
  return activeToken;
}

/**
 * Sets the MSAL instance and active account so the API layer can
 * silently refresh expired tokens on 401 responses. Pass null to clear
 * the instance (e.g. on logout or in tests). Resets the redirect guard
 * and any pending refresh so a new session starts clean.
 */
export function setMsalInstance(instance: IPublicClientApplication | null, account: AccountInfo | null): void {
  msalInstance = instance;
  msalAccount = account;
  isRedirecting = false;
  refreshPromise = null;
}

/**
 * Decodes the JWT payload (without signature verification) to extract claims.
 * Used only for client-side expiry checks — never for auth decisions.
 */
function decodeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    // Base64url → Base64 (restore padding) → decode
    const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    // JWTs omit the '=' padding; atob requires it.
    const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4);
    const json = atob(padded);
    return JSON.parse(json);
  } catch {
    return null;
  }
}

/**
 * Returns true if the token's exp claim is in the past (within leeway).
 */
export function isTokenExpired(token: string, leewaySeconds = 0): boolean {
  const payload = decodeJwtPayload(token);
  if (!payload || typeof payload.exp !== 'number') return true;
  return Date.now() >= (payload.exp - leewaySeconds) * 1000;
}

/**
 * Returns seconds until the token expires, or 0 if already expired / unparseable.
 */
export function secondsUntilExpiry(token: string): number {
  const payload = decodeJwtPayload(token);
  if (!payload || typeof payload.exp !== 'number') return 0;
  return Math.max(0, payload.exp - Math.floor(Date.now() / 1000));
}

/**
 * Silently refreshes the idToken via MSAL. Deduplicates concurrent calls
 * so that multiple simultaneous 401s (or a proactive renewal + a 401) only
 * trigger a single acquireTokenSilent. On failure, triggers
 * acquireTokenRedirect exactly once (guarded by isRedirecting).
 *
 * This is the single source of truth for token refresh — both the reactive
 * 401 handler in createApiFetch and the proactive renewal timer in
 * AuthContext call this function.
 */
export async function refreshAccessToken(): Promise<string | null> {
  if (isRedirecting) return null;
  if (refreshPromise) return refreshPromise;
  if (!msalInstance || !msalAccount) return null;

  refreshPromise = (async () => {
    try {
      const response = await msalInstance!.acquireTokenSilent({
        ...loginRequest,
        account: msalAccount!,
      });
      if (response.idToken) {
        activeToken = response.idToken;
        return response.idToken;
      }
      return null;
    } catch (error) {
      console.error('Silent token refresh failed:', error);
      // Trigger interactive re-login via redirect exactly once. Set the
      // flag BEFORE the check so concurrent callers can't slip through the
      // gap between the check and the assignment (JS is single-threaded,
      // but the check-then-act pattern is still vulnerable to interleaving
      // across awaited boundaries). We set the flag unconditionally here
      // because we're already inside the single deduplicated refresh path.
      if (!isRedirecting) {
        isRedirecting = true;
        if (msalInstance) {
          msalInstance.acquireTokenRedirect(loginRequest);
          // Safety reset: if the redirect doesn't actually navigate within
          // 10s (edge case where the redirect is blocked or fails silently),
          // clear the guard so the user can retry instead of being stuck.
          setTimeout(() => { isRedirecting = false; }, 10000);
        } else {
          // No MSAL instance — clear the flag so future calls aren't blocked.
          isRedirecting = false;
        }
      }
      return null;
    } finally {
      // Delay clearing refreshPromise by a microtask so that all concurrent
      // callers awaiting this promise have resolved before we allow a new
      // refresh to start. Without this, a caller that checks refreshPromise
      // after the finally runs but before its own continuation executes would
      // start a redundant second refresh.
      setTimeout(() => { refreshPromise = null; }, 0);
    }
  })();

  return refreshPromise;
}

/**
 * Builds the standard request headers, injecting the bearer token when set.
 */
function getHeaders(): HeadersInit {
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  };
  if (activeToken) {
    headers['Authorization'] = `Bearer ${activeToken}`;
  }
  return headers;
}

/**
 * Creates a fetch wrapper bound to a base URL with auth headers,
 * 401 silent-refresh-retry, and consistent error parsing.
 *
 * The returned function takes a path (appended to baseURL) and standard
 * RequestInit options, returning parsed JSON.
 */
export function createApiFetch(baseURL: string) {
  return async function fetchWithAuth<T>(path: string, options: RequestInit = {}): Promise<T> {
    const doFetch = async (): Promise<Response> =>
      fetch(`${baseURL}${path}`, {
        ...options,
        headers: { ...getHeaders(), ...options.headers },
      });

    let response = await doFetch();

    // If we got a 401 and we have an MSAL instance, try refreshing the token
    // and retrying once. refreshAccessToken() deduplicates concurrent calls,
    // so multiple simultaneous 401s only trigger a single acquireTokenSilent.
    // If silent refresh also fails, refreshAccessToken() triggers an
    // interactive redirect exactly once (guarded by isRedirecting).
    if (response.status === 401 && msalInstance) {
      const newToken = await refreshAccessToken();
      if (newToken) {
        response = await doFetch();
      }
    }

    if (!response.ok) {
      let errorMessage = `HTTP Error ${response.status}`;
      try {
        const errorData = await response.json();
        errorMessage = errorData.detail || errorMessage;
      } catch {
        // JSON parsing failed, keep basic message
      }
      throw new Error(errorMessage);
    }

    return response.json() as Promise<T>;
  };
}
