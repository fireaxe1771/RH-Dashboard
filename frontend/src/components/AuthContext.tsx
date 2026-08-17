import React, { createContext, useContext, useState, useEffect, useRef, useCallback } from 'react';
import { useMsal, useIsAuthenticated } from '@azure/msal-react';
import { loginRequest } from '../authConfig';
import { setAuthToken, setMsalInstance, getAuthToken, refreshAccessToken, isTokenExpired, secondsUntilExpiry } from '../services/api';
import { AlertTriangle } from 'lucide-react';

export interface UserProfile {
  name: string;
  email: string;
}

interface AuthContextType {
  user: UserProfile | null;
  isAuthenticated: boolean;
  loading: boolean;
  login: () => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { instance, accounts, inProgress } = useMsal();
  const isMsalAuthenticated = useIsAuthenticated();
  const isDevAuthBypass = import.meta.env.VITE_DEV_AUTH_BYPASS === 'true';
  const [configError, setConfigError] = useState<string | null>(null);

  // Tracks whether we've acquired a valid token and set it in the API layer.
  // The app must not render authenticated content (and fire API calls) until
  // this is true, otherwise requests go out with no Authorization header.
  const [tokenReady, setTokenReady] = useState(isDevAuthBypass);

  // Guards against duplicate handleRedirectPromise / acquireTokenSilent calls.
  const hasHandledRedirect = useRef(false);
  const isAcquiringToken = useRef(false);
  const isLoggingOut = useRef(false);

  // Proactive token renewal timer. Scheduled after each successful token
  // acquisition to renew ~5 minutes before the idToken expires, so the user
  // never sees a 401 from an expired token on a tab that's been left open.
  const renewalTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Validate environment variables on startup
  useEffect(() => {
    const clientId = import.meta.env.VITE_AZURE_CLIENT_ID;
    const tenantId = import.meta.env.VITE_AZURE_TENANT_ID;

    // Fail loudly if Entra ID configuration is missing
    if (isDevAuthBypass) {
      setConfigError(null);
    } else if (!clientId) {
      setConfigError("VITE_AZURE_CLIENT_ID is not configured in the environment variables.");
    } else if (!tenantId) {
      setConfigError("VITE_AZURE_TENANT_ID is not configured in the environment variables.");
    }
  }, []);

  // Keep the API layer's MSAL reference in sync so it can silently refresh
  // expired tokens on 401 responses without bouncing the user to re-login.
  useEffect(() => {
    if (isDevAuthBypass) return;
    setMsalInstance(instance, accounts.length > 0 ? accounts[0] : null);
  }, [instance, accounts, isDevAuthBypass]);

  // Acquire the idToken for API authentication.
  //
  // This single effect handles both the redirect-return flow (after
  // loginRedirect bounces the user to Microsoft and back) and the silent
  // cache-hit flow (page refresh with a valid cached session). We call
  // handleRedirectPromise first — it resolves with the auth result if we're
  // returning from a redirect, or null if we're loading normally. After it
  // resolves, we attempt acquireTokenSilent to get a fresh idToken.
  //
  // Running these sequentially (not in parallel) prevents the race condition
  // where acquireTokenSilent fires before MSAL has processed the redirect
  // response and loaded the cache, causing a spurious failure and an
  // unnecessary full redirect to login.microsoftonline.com.
  useEffect(() => {
    if (isDevAuthBypass) {
      setAuthToken(null);
      setTokenReady(true);
      return;
    }

    // Do not start or continue token acquisition while logout is redirecting.
    if (isLoggingOut.current) return;
    if (hasHandledRedirect.current) return;
    hasHandledRedirect.current = true;

    let cancelled = false;

    (async () => {
      try {
        // Process any pending redirect response first.
        const redirectResult = await instance.handleRedirectPromise();
        if (cancelled || isLoggingOut.current) return;

        // If we got a result from the redirect, extract the idToken.
        if (redirectResult && redirectResult.idToken) {
          setAuthToken(redirectResult.idToken);
          setTokenReady(true);
          return;
        }

        // No redirect result — try silent token acquisition from cache.
        // This handles the page-refresh case where MSAL has a cached account.
        if (accounts.length > 0) {
          isAcquiringToken.current = true;
          try {
            const response = await instance.acquireTokenSilent({
              ...loginRequest,
              account: accounts[0],
            });
            if (cancelled) return;

            // Use the idToken (aud=client_id) instead of the accessToken
            // (aud=Graph for User.Read scope). The backend validates the
            // token audience against the SPA client ID, so the idToken is
            // the correct token to send for API authentication.
            if (!response.idToken) {
              console.error("acquireTokenSilent response missing idToken, cannot authenticate");
              setAuthToken(null);
              setTokenReady(false);
              return;
            }

            // MSAL can return a cached response with an expired idToken
            // when the Graph access token (scoped to User.Read) is still
            // valid. Force a refresh to get a fresh idToken from AAD.
            if (isTokenExpired(response.idToken, 30)) {
              const refreshed = await instance.acquireTokenSilent({
                ...loginRequest,
                account: accounts[0],
                forceRefresh: true,
              });
              if (cancelled) return;
              if (!refreshed.idToken || isTokenExpired(refreshed.idToken, 30)) {
                console.error("Could not acquire a valid (non-expired) idToken even after forceRefresh");
                setAuthToken(null);
                setTokenReady(false);
                return;
              }
              setAuthToken(refreshed.idToken);
              setTokenReady(true);
              return;
            }

            setAuthToken(response.idToken);
            setTokenReady(true);
          } catch (error) {
            if (cancelled || isLoggingOut.current) return;
            console.error("acquireTokenSilent failed, attempting redirect:", error);
            // Force a full re-login via redirect.
            await instance.acquireTokenRedirect(loginRequest);
          } finally {
            isAcquiringToken.current = false;
          }
        } else {
          // No cached account — user is not authenticated.
          setAuthToken(null);
          setTokenReady(false);
        }
      } catch (error) {
        if (cancelled) return;
        console.error("handleRedirectPromise failed:", error);
        setAuthToken(null);
        setTokenReady(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [instance, isDevAuthBypass, accounts]);

  // Re-acquire token silently when MSAL reports a cached account on a
  // subsequent render (e.g. MSAL finishes loading its cache asynchronously).
  useEffect(() => {
    if (isDevAuthBypass || isLoggingOut.current || hasHandledRedirect.current) return;
    if (!isMsalAuthenticated || accounts.length === 0) return;
    if (isAcquiringToken.current) return;
    if (tokenReady) return;

    isAcquiringToken.current = true;
    instance
      .acquireTokenSilent({
        ...loginRequest,
        account: accounts[0],
      })
      .then(async (response) => {
        if (!response.idToken) {
          console.error("acquireTokenSilent response missing idToken");
          setAuthToken(null);
          setTokenReady(false);
          return;
        }
        // MSAL can return a cached response with an expired idToken when
        // the Graph access token is still valid. Force a refresh.
        if (isTokenExpired(response.idToken, 30)) {
          const refreshed = await instance.acquireTokenSilent({
            ...loginRequest,
            account: accounts[0],
            forceRefresh: true,
          });
          if (!refreshed.idToken || isTokenExpired(refreshed.idToken, 30)) {
            console.error("Could not acquire a valid idToken after forceRefresh");
            setAuthToken(null);
            setTokenReady(false);
            return;
          }
          setAuthToken(refreshed.idToken);
          setTokenReady(true);
          return;
        }
        setAuthToken(response.idToken);
        setTokenReady(true);
      })
      .catch((error) => {
        if (isLoggingOut.current) return;
        console.error("Token re-acquisition failed, attempting redirect:", error);
        void instance.acquireTokenRedirect(loginRequest);
      })
      .finally(() => {
        isAcquiringToken.current = false;
      });
  }, [isDevAuthBypass, isMsalAuthenticated, accounts, instance, tokenReady]);

  // Proactive token renewal: schedule a silent refresh ~5 minutes before the
  // idToken expires so the user never hits a 401 from an expired token on a
  // tab that's been left open. Uses the shared refreshAccessToken() from
  // fetchWrapper so the refresh is deduplicated with any concurrent 401 retry.
  //
  // CLOCK_SKEW_SECONDS adds a buffer to compensate for client/server clock
  // drift. If the client clock is behind the server, the token's exp claim
  // (set by AAD) will appear further in the future than it actually is,
  // so we renew earlier to avoid a window where the token is expired
  // server-side but not yet client-side.
  const CLOCK_SKEW_SECONDS = 60;
  const RENEW_BEFORE_EXPIRY_SECONDS = 300; // 5 minutes
  const MIN_RENEWAL_DELAY_SECONDS = 30;

  const scheduleRenewal = useCallback(() => {
    if (renewalTimer.current) {
      clearTimeout(renewalTimer.current);
      renewalTimer.current = null;
    }
    if (isDevAuthBypass || isLoggingOut.current) return;

    const token = getAuthToken();
    if (!token) return;

    const secondsLeft = secondsUntilExpiry(token);
    if (secondsLeft <= 0) return; // Already expired — let the 401 handler deal with it

    // Renew (RENEW_BEFORE_EXPIRY_SECONDS + CLOCK_SKEW_SECONDS) before expiry,
    // with a minimum delay so we don't fire immediately for short-lived tokens.
    const renewInSeconds = Math.max(
      MIN_RENEWAL_DELAY_SECONDS,
      secondsLeft - RENEW_BEFORE_EXPIRY_SECONDS - CLOCK_SKEW_SECONDS,
    );
    renewalTimer.current = setTimeout(async () => {
      const newToken = await refreshAccessToken();
      if (newToken) {
        // refreshAccessToken already updated activeToken in the fetchWrapper;
        // reschedule for the new token's expiry.
        scheduleRenewal();
      } else {
        // refreshAccessToken returned null: either silent refresh failed
        // (and acquireTokenRedirect was triggered, which will reload the
        // page) or there's no MSAL instance. Log for observability so
        // transient failures aren't silently swallowed — the reactive 401
        // handler will still catch the expired token, but this helps
        // diagnose why proactive renewal didn't prevent it.
        console.warn('Proactive token renewal returned null; relying on reactive 401 retry');
      }
    }, renewInSeconds * 1000);
  }, [isDevAuthBypass]);

  // Schedule renewal when token becomes ready; clear on logout/unmount.
  useEffect(() => {
    if (tokenReady && !isDevAuthBypass) {
      scheduleRenewal();
    }
    return () => {
      if (renewalTimer.current) {
        clearTimeout(renewalTimer.current);
        renewalTimer.current = null;
      }
    };
  }, [tokenReady, isDevAuthBypass, scheduleRenewal]);

  const user: UserProfile | null = isMsalAuthenticated && accounts.length > 0
    ? {
        name: accounts[0].name || accounts[0].username,
        email: accounts[0].username,
      }
    : (isDevAuthBypass
        ? {
            name: 'Local Dev User',
            email: 'dev.local@streamlineas.com',
          }
        : null);

  // The app is "loading" until MSAL finishes any in-progress interaction
  // AND we have a token ready (or we know the user is unauthenticated).
  const isAuthLoading = isDevAuthBypass
    ? false
    : (inProgress !== 'none' || (isMsalAuthenticated && !tokenReady));

  const login = async () => {
    if (isDevAuthBypass) {
      return;
    }

    if (configError) {
      console.error("Login halted: Application is misconfigured.");
      return;
    }
    // Use redirect instead of popup. The popup flow fails in Azure Container
    // Apps because login.microsoftonline.com sets Cross-Origin-Opener-Policy
    // headers that block MSAL's window.closed monitor, causing the popup to
    // hang silently and no token to ever be acquired.
    await instance.loginRedirect(loginRequest);
  };

  const logout = async () => {
    if (isDevAuthBypass || !isMsalAuthenticated || isLoggingOut.current) {
      return;
    }

    isLoggingOut.current = true;
    setAuthToken(null);
    setTokenReady(false);
    // Cancel any pending proactive renewal timer.
    if (renewalTimer.current) {
      clearTimeout(renewalTimer.current);
      renewalTimer.current = null;
    }
    // Prevent the API layer from attempting a silent refresh while the
    // logout redirect is in progress.
    setMsalInstance(instance, null);

    try {
      await instance.logoutRedirect({
        postLogoutRedirectUri: window.location.origin,
      });
    } catch (error) {
      // Restore the guard if logout fails before navigation, so the user can
      // retry instead of leaving the auth provider permanently blocked.
      isLoggingOut.current = false;
      setMsalInstance(instance, accounts[0] ?? null);
      console.error("Logout failed:", error);
      throw error;
    }
  };

  // Fail Loudly UI for configuration errors
  if (configError) {
    return (
      <div 
        style={{
          height: '100vh',
          width: '100vw',
          backgroundColor: '#0f172a',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '24px',
          fontFamily: "'Inter', sans-serif"
        }}
      >
        <div 
          style={{
            maxWidth: '500px',
            backgroundColor: '#1e293b',
            border: '1px solid rgba(239, 68, 68, 0.3)',
            borderRadius: '12px',
            padding: '32px',
            boxShadow: '0 10px 25px rgba(0,0,0,0.5)',
            textAlign: 'center',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: '16px'
          }}
        >
          <AlertTriangle size={48} style={{ color: '#ef4444' }} />
          <h1 style={{ color: 'white', fontSize: '20px', fontWeight: 700 }}>Fatal Configuration Error</h1>
          <p style={{ color: '#94a3b8', fontSize: '14px', lineHeight: '1.5' }}>
            {configError}
          </p>
          <p style={{ color: '#64748b', fontSize: '12px' }}>
            Please check your local <code>.env</code> file or Azure App Container configuration variables and reboot the application.
          </p>
        </div>
      </div>
    );
  }

  return (
    <AuthContext.Provider
      value={{
        user,
        // Only report as authenticated once the token is ready, so the app
        // doesn't render authenticated content and fire API calls before
        // the Authorization header is set.
        isAuthenticated: isDevAuthBypass ? true : (isMsalAuthenticated && tokenReady),
        loading: isAuthLoading,
        login,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
