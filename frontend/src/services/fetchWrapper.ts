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
 * the instance (e.g. on logout or in tests).
 */
export function setMsalInstance(instance: IPublicClientApplication | null, account: AccountInfo | null): void {
  msalInstance = instance;
  msalAccount = account;
}

/**
 * Attempts to silently refresh the idToken via MSAL. Returns the new token
 * or null if refresh fails. On refresh failure, triggers an interactive
 * redirect to re-login instead of leaving the user with a generic 401 error.
 */
async function refreshToken(): Promise<string | null> {
  if (!msalInstance || !msalAccount) return null;
  try {
    const response = await msalInstance.acquireTokenSilent({
      ...loginRequest,
      account: msalAccount,
    });
    if (response.idToken) {
      activeToken = response.idToken;
      return response.idToken;
    }
  } catch (error) {
    console.error('Silent token refresh failed on 401:', error);
    // Trigger interactive re-login via redirect instead of leaving the
    // user with a generic 401 error. MSAL handles the redirect; the page
    // will reload after re-auth.
    msalInstance.acquireTokenRedirect(loginRequest);
  }
  return null;
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
    // and retrying once. This handles expired tokens gracefully without
    // bouncing the user to a full re-login (unless silent refresh also fails,
    // in which case refreshToken() triggers an interactive redirect).
    if (response.status === 401 && msalInstance) {
      const newToken = await refreshToken();
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
