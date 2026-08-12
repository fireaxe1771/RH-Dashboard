import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import { useMsal, useIsAuthenticated } from '@azure/msal-react';
import { loginRequest } from '../authConfig';
import { setAuthToken, setMsalInstance } from '../services/api';
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
      .then((response) => {
        if (!response.idToken) {
          console.error("acquireTokenSilent response missing idToken");
          setAuthToken(null);
          setTokenReady(false);
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
