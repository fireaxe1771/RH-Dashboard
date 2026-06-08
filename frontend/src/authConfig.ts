import { Configuration, PopupRequest } from "@azure/msal-browser";

const isDevAuthBypass = import.meta.env.VITE_DEV_AUTH_BYPASS === 'true';

/**
 * Configuration object to be passed to MSAL instance on creation.
 * For details, visit: https://github.com/AzureAD/microsoft-authentication-library-for-js/blob/dev/lib/msal-browser/docs/configuration.md
 */
export const msalConfig: Configuration = {
  auth: {
    // These will be supplied by the user during deployment.
    // We default to template configurations or environment variables.
    clientId: isDevAuthBypass ? "local-dev-client-id" : (import.meta.env.VITE_AZURE_CLIENT_ID || ""),
    authority: isDevAuthBypass
      ? "https://login.microsoftonline.com/common"
      : `https://login.microsoftonline.com/${import.meta.env.VITE_AZURE_TENANT_ID || ""}`,
    redirectUri: window.location.origin,
    postLogoutRedirectUri: window.location.origin,
  },
  cache: {
    cacheLocation: "sessionStorage", // This configures where your cache will be stored
    storeAuthStateInCookie: false, // Set this to "true" if you are having issues on IE11 or Edge
  }
};

/**
 * Scopes you add here will be prompted for user consent during sign-in.
 * By default, MSAL.js will add OIDC scopes (openid, profile, email) to any login request.
 * For more information about OIDC scopes, visit: 
 * https://docs.microsoft.com/en-us/azure/active-directory/develop/v2-permissions-and-consent#openid-connect-scopes
 */
export const loginRequest: PopupRequest = {
  scopes: ["User.Read"]
};

/**
 * Add here the endpoints and scopes for active services you wish to query.
 */
export const graphConfig = {
  graphMeEndpoint: "https://graph.microsoft.com/v1.0/me"
};
