import React, { createContext, useContext, useState, useEffect } from 'react';
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
  const [configError, setConfigError] = useState<string | null>(null);

  // Validate environment variables on startup
  useEffect(() => {
    const clientId = import.meta.env.VITE_AZURE_CLIENT_ID;
    const tenantId = import.meta.env.VITE_AZURE_TENANT_ID;

    // Fail loudly if Entra ID configuration is missing
    if (!clientId) {
      setConfigError("VITE_AZURE_CLIENT_ID is not configured in the environment variables.");
    } else if (!tenantId) {
      setConfigError("VITE_AZURE_TENANT_ID is not configured in the environment variables.");
    }
  }, []);

  // Sync MSAL access tokens to the API service layer
  useEffect(() => {
    if (isMsalAuthenticated && accounts.length > 0) {
      instance
        .acquireTokenSilent({
          ...loginRequest,
          account: accounts[0],
        })
        .then((response) => {
          setAuthToken(response.accessToken);
        })
        .catch((error) => {
          console.error("Acquiring token silently failed, attempting redirect:", error);
          instance.acquireTokenRedirect(loginRequest);
        });
    } else {
      setAuthToken(null);
    }
  }, [isMsalAuthenticated, accounts, instance]);

  const user: UserProfile | null = isMsalAuthenticated && accounts.length > 0
    ? {
        name: accounts[0].name || accounts[0].username,
        email: accounts[0].username,
      }
    : null;

  const isAuthLoading = inProgress !== 'none';

  const login = async () => {
    if (configError) {
      console.error("Login halted: Application is misconfigured.");
      return;
    }
    try {
      await instance.loginPopup(loginRequest);
    } catch (error) {
      console.error("MSAL Popup Login failed, falling back to redirect:", error);
      await instance.loginRedirect(loginRequest);
    }
  };

  const logout = async () => {
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
        isAuthenticated: isMsalAuthenticated,
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
