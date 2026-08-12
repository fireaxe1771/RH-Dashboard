import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import { useMsal, useIsAuthenticated } from '@azure/msal-react';
import { loginRequest } from '../authConfig';
import { setAuthToken } from '../services/api';
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
  const hasHandledRedirect = useRef(false);

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

  // Sync MSAL access tokens to the API service layer
  useEffect(() => {
    if (isDevAuthBypass) {
      setAuthToken(null);
      return;
    }

    // Capture the token from the redirect promise result (runs once after
    // the browser is redirected back from login.microsoftonline.com).
    // handleRedirectPromise is idempotent, but we guard with a ref so the
    // effect doesn't re-run on every render where instance changes identity.
    if (hasHandledRedirect.current) return;
    hasHandledRedirect.current = true;

    instance.handleRedirectPromise()
      .then((result) => {
        if (result && result.idToken) {
          setAuthToken(result.idToken);
        }
      })
      .catch((error) => {
        console.error("handleRedirectPromise failed:", error);
      });
  }, [instance, isDevAuthBypass]);

  // Acquire token silently when authenticated (covers page reloads with
  // cached session state).
  useEffect(() => {
    if (isDevAuthBypass) {
      setAuthToken(null);
      return;
    }

    if (isMsalAuthenticated && accounts.length > 0) {
      instance
        .acquireTokenSilent({
          ...loginRequest,
          account: accounts[0],
        })
        .then((response) => {
          // Use the idToken (aud=client_id) instead of the accessToken
          // (aud=Graph for User.Read scope). The backend validates the
          // token audience against the SPA client ID, so the idToken is
          // the correct token to send for API authentication.
          if (!response.idToken) {
            console.error("acquireTokenSilent response missing idToken, cannot authenticate");
            setAuthToken(null);
            return;
          }
          setAuthToken(response.idToken);
        })
        .catch((error) => {
          console.error("Acquiring token silently failed, attempting redirect:", error);
          instance.acquireTokenRedirect(loginRequest);
        });
    } else if (!isMsalAuthenticated) {
      setAuthToken(null);
    }
  }, [isDevAuthBypass, isMsalAuthenticated, accounts, instance]);

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

  const isAuthLoading = isDevAuthBypass ? false : inProgress !== 'none';

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
    if (isDevAuthBypass) {
      return;
    }

    if (isMsalAuthenticated) {
      await instance.logoutRedirect({
        postLogoutRedirectUri: window.location.origin,
      });
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
        isAuthenticated: isDevAuthBypass ? true : isMsalAuthenticated,
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
