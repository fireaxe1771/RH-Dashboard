# Task: Authentication Hardening — RecoveryHub Dashboard

You are working on the RecoveryHub Dashboard project at `E:\gitrepo\RH Dashboard`.
This is a FastAPI backend + React/TypeScript frontend (Vite) deployed to Azure
Container Apps. Authentication uses MSAL.js (Entra ID / Azure AD) on the
frontend and JWT validation on the backend.

Your job is to fix all authentication hardening issues identified in a
security review. Implement every fix listed below. Do NOT skip any. After
all fixes are implemented, run the test suites to verify nothing broke.

## Project context (read these files first to understand the codebase)

- `AGENTS.md` — project notes, build/test commands, deploy notes
- `backend/config.py` — settings via pydantic-settings
- `backend/auth.py` — JWT validation (TokenVerifier, get_current_user)
- `backend/main.py` — FastAPI app, CORS middleware, lifespan
- `frontend/src/authConfig.ts` — MSAL configuration
- `frontend/src/components/AuthContext.tsx` — auth provider, token acquisition
- `frontend/src/services/api.ts` — core API client with 401-refresh-retry
- `frontend/src/services/billingApi.ts` — billing API client (NO 401 retry)
- `frontend/src/services/aiAnalyticsApi.ts` — AI analytics API client (NO 401 retry)
- `frontend/src/services/aiAdoptionApi.ts` — AI adoption API client (NO 401 retry)
- `frontend/Dockerfile` — frontend build (Vite build args)
- `terraform/main.tf` — Container Apps infra (env vars for backend)
- `terraform/variables.tf` — Terraform variables
- `.github/workflows/deploy.yml` — deploy workflow (build args, TF vars)

## Build & test commands

```powershell
# Backend tests (from backend/)
$env:TESTING="true"; .venv\Scripts\python.exe -m pytest -v

# Frontend tests (from frontend/)
npm run test

# Frontend build (from frontend/)
npm run build
```

---

## FIX 1 (HIGH): Restrict CORS to the frontend origin in production

**File:** `backend/main.py` (around line 81-87)

**Current code:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to FQDN domain of container app
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Problem:** `allow_origins=["*"]` with `allow_credentials=True` is a CSRF
risk and causes "quirky" cross-origin auth behavior.

**Fix:**
1. Add a new setting `FRONTEND_URL` to `backend/config.py`:
   ```python
   FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")
   ```
   (Follow the existing pattern in config.py for how other env vars are
   declared. It should default to localhost for local dev.)
2. In `backend/main.py`, replace the CORS middleware with:
   ```python
   app.add_middleware(
       CORSMiddleware,
       allow_origins=[settings.FRONTEND_URL],
       allow_credentials=True,
       allow_methods=["*"],
       allow_headers=["*"],
   )
   ```
3. Add `FRONTEND_URL` as a Terraform variable in `terraform/variables.tf`
   (type string, no default — it's environment-specific) and pass it to the
   backend container app in `terraform/main.tf` as an env var:
   ```hcl
   env {
     name  = "FRONTEND_URL"
     value = var.frontend_url
   }
   ```
4. Add `TF_VAR_frontend_url` to `.github/workflows/deploy.yml` in the
   Terraform env block, sourced from a GitHub secret `FRONTEND_URL`:
   ```yaml
   TF_VAR_frontend_url: ${{ secrets.FRONTEND_URL }}
   ```
5. Add `FRONTEND_URL=http://localhost:3000` to `.env.example` with a comment
   explaining it must be set to the frontend FQDN in production.

---

## FIX 2 (HIGH): Single source of truth for SPA Client ID

**Files:**
- `.github/workflows/deploy.yml` (line ~96, hardcoded in `--build-arg`)
- `terraform/variables.tf` (line ~98-102, `azure_spa_client_id` default)
- `.env.example`

**Current state:** The SPA client ID `d7d4d4d0-5460-4655-ab6d-a9aaac38b578`
is hardcoded in the deploy workflow build arg, the Terraform default, and the
example env. The backend's JWT audience validation
(`backend/auth.py` line ~21, `self.client_id = settings.AZURE_CLIENT_ID`)
depends on this value being consistent with the frontend's
`VITE_AZURE_CLIENT_ID`.

**Fix:**
1. In `terraform/variables.tf`, **remove the hardcoded default** from
   `azure_spa_client_id` so it must be explicitly provided:
   ```hcl
   variable "azure_spa_client_id" {
     type        = string
     description = "Azure Entra ID SPA App Registration client ID used by the frontend for MSAL login. The backend validates JWT audience against this value."
   }
   ```
2. In `.github/workflows/deploy.yml`, replace the hardcoded build arg:
   ```yaml
   --build-arg VITE_AZURE_CLIENT_ID=${{ secrets.AZURE_SPA_CLIENT_ID }} \
   ```
   And add to the Terraform env block:
   ```yaml
   TF_VAR_azure_spa_client_id: ${{ secrets.AZURE_SPA_CLIENT_ID }}
   ```
3. Add a note to `.env.example` that `VITE_AZURE_CLIENT_ID` and
   `AZURE_CLIENT_ID` (backend) must reference the same SPA app registration
   client ID, and that in production this comes from the
   `AZURE_SPA_CLIENT_ID` GitHub secret.
4. Add a clarifying comment in `terraform/main.tf` where
   `AZURE_CLIENT_ID` is set to `var.azure_spa_client_id` for the backend
   container, explaining: "The backend validates the SPA's idToken audience
   against this client ID. This is intentional — the frontend sends the
   idToken (aud=SPA client ID) as the bearer token, and the backend verifies
   that same audience."

**Note:** This requires the user to create a GitHub secret named
`AZURE_SPA_CLIENT_ID` with the value
`d7d4d4d0-5460-4655-ab6d-a9aaac38b578` (or the rotated equivalent). Document
this in the PR description.

---

## FIX 3 (MEDIUM): Redirect to login on 401 token refresh failure

**File:** `frontend/src/services/api.ts` (lines ~98-113, `refreshToken()`)

**Current code:**
```typescript
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
    console.error("Silent token refresh failed on 401:", error);
  }
  return null;
}
```

**Problem:** When silent refresh fails, it returns `null` and the 401
propagates as a generic "HTTP Error 401" to the UI. The user sees a confusing
error instead of being redirected to re-login. This is a primary cause of
"quirky" auth UX.

**Fix:** On refresh failure, trigger an interactive redirect to re-login:
```typescript
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
    console.error("Silent token refresh failed on 401:", error);
    // Trigger interactive re-login via redirect instead of leaving the
    // user with a generic 401 error. MSAL handles the redirect; the page
    // will reload after re-auth.
    msalInstance.acquireTokenRedirect(loginRequest);
  }
  return null;
}
```

---

## FIX 4 (MEDIUM): Increase JWKS fetch timeout and add retry

**File:** `backend/auth.py` (lines ~37-44, `_fetch_jwks`)

**Current code:**
```python
def _fetch_jwks(self) -> dict:
    url = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
    try:
        logger.info("Downloading active Microsoft JWKS public key set...")
        with urllib.request.urlopen(url, timeout=5) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        logger.error(f"Failed to fetch Microsoft signing keys from AAD: {e}")
        raise RuntimeError(f"Entra ID Security Catalog Unreachable: {e}")
```

**Problem:** 5s timeout is too short for cold ACA replicas or slow networks.
No retry on transient failures.

**Fix:** Increase timeout to 15s and add a simple retry (2 attempts with a
short sleep). Keep the existing fail-loud behavior on final failure. Example:
```python
import time

def _fetch_jwks(self) -> dict:
    url = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
    max_attempts = 2
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(f"Downloading Microsoft JWKS (attempt {attempt}/{max_attempts})...")
            with urllib.request.urlopen(url, timeout=15) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            last_error = e
            logger.warning(f"JWKS fetch attempt {attempt} failed: {e}")
            if attempt < max_attempts:
                time.sleep(1)
    logger.error(f"Failed to fetch Microsoft signing keys from AAD after {max_attempts} attempts: {last_error}")
    raise RuntimeError(f"Entra ID Security Catalog Unreachable: {last_error}")
```

---

## FIX 5 (LOW): Use library issuer verification instead of manual check

**File:** `backend/auth.py` (lines ~94-108, `verify_token`)

**Current code:**
```python
issuers = [
    f"https://login.microsoftonline.com/{self.tenant_id}/v2.0",
    f"https://sts.windows.net/{self.tenant_id}/"
]
try:
    payload = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        audience=self.client_id,
        options={"verify_aud": True, "verify_iss": False, "verify_exp": True}
    )
    iss = payload.get("iss")
    if iss not in issuers:
        raise JWTError(f"Issuer '{iss}' does not match expected tenant options.")
    return payload
except JWTError as e:
    ...
```

**Problem:** Disabling `verify_iss` then manually checking is fragile and
confusing. The `python-jose` library supports passing a list of acceptable
issuers directly.

**Fix:** Verify whether the installed `python-jose` version supports the
`issuer` parameter accepting a list. If it does, use:
```python
payload = jwt.decode(
    token,
    public_key,
    algorithms=["RS256"],
    audience=self.client_id,
    issuer=issuers,
    options={"verify_aud": True, "verify_iss": True, "verify_exp": True}
)
return payload
```
If the installed version does NOT support a list for `issuer` (only a single
string), keep the manual verification but add a clear comment explaining why:
```python
# python-jose's `issuer` param accepts only a single string, not a list,
# so we disable library issuer verification and check manually against
# both valid Entra ID issuer formats.
```
Run the backend tests after this change to confirm behavior. Check
`backend/requirements.txt` for the installed `python-jose` version.

---

## FIX 6 (LOW): Document VITE_DEV_AUTH_BYPASS exclusion in Dockerfile

**File:** `frontend/Dockerfile`

**Current state:** `VITE_DEV_AUTH_BYPASS` is correctly NOT declared as an
ARG, so it can't leak into production builds. This is good but not
documented.

**Fix:** Add a security comment block after the existing ARG declarations
(line ~6):
```dockerfile
# SECURITY NOTE: VITE_DEV_AUTH_BYPASS is intentionally NOT declared as a
# build arg here, so it cannot be baked into production images. It should
# only be used in local development via docker-compose or direct npm run dev.
# Do NOT add it as an ARG without explicit security review.
```

---

## FIX 7 (HIGH/DRY): Shared fetch wrapper with 401-refresh-retry for ALL API clients

**Files:**
- `frontend/src/services/api.ts` (has 401 retry — keep as the reference)
- `frontend/src/services/billingApi.ts` (NO 401 retry)
- `frontend/src/services/aiAnalyticsApi.ts` (NO 401 retry)
- `frontend/src/services/aiAdoptionApi.ts` (NO 401 retry)

**Problem:** Each of the four service files reimplements its own fetch
wrapper with auth-header injection and error parsing. Only `api.ts` has the
401->silent-refresh->retry logic. The other three silently fail on expired
tokens instead of refreshing. This is both a DRY violation and an auth
hardening bug.

**Fix:**
1. Create `frontend/src/services/fetchWrapper.ts` exporting a factory:
   ```typescript
   import { AccountInfo, IPublicClientApplication } from '@azure/msal-browser';
   import { loginRequest } from '../authConfig';

   let activeToken: string | null = null;
   let msalInstance: IPublicClientApplication | null = null;
   let msalAccount: AccountInfo | null = null;

   export function setAuthToken(token: string | null): void {
     activeToken = token;
   }

   export function getAuthToken(): string | null {
     return activeToken;
   }

   export function setMsalInstance(instance: IPublicClientApplication, account: AccountInfo | null): void {
     msalInstance = instance;
     msalAccount = account;
   }

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
       msalInstance.acquireTokenRedirect(loginRequest);
     }
     return null;
   }

   function getHeaders(): HeadersInit {
     const headers: HeadersInit = { 'Content-Type': 'application/json' };
     if (activeToken) {
       headers['Authorization'] = `Bearer ${activeToken}`;
     }
     return headers;
   }

   /**
    * Creates a fetch wrapper bound to a base URL with auth headers,
    * 401 silent-refresh-retry, and consistent error parsing.
    */
   export function createApiFetch(baseURL: string) {
     return async function fetchWithAuth<T>(path: string, options: RequestInit = {}): Promise<T> {
       const doFetch = async (): Promise<Response> =>
         fetch(`${baseURL}${path}`, {
           ...options,
           headers: { ...getHeaders(), ...options.headers },
         });

       let response = await doFetch();
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
           // keep default message
         }
         throw new Error(errorMessage);
       }
       return response.json() as Promise<T>;
     };
   }
   ```

2. Refactor `frontend/src/services/api.ts` to import from `fetchWrapper.ts`
   and use `createApiFetch('/api')`. Keep all the existing type exports
   (`Widget`, `Dashboard`, `QueryResult`, etc.) and the `api` object's
   public method signatures unchanged. Re-export `setAuthToken`,
   `getAuthToken`, `setMsalInstance` from `fetchWrapper.ts` so existing
   imports from `api.ts` still work.

3. Refactor `billingApi.ts`, `aiAnalyticsApi.ts`, `aiAdoptionApi.ts` to use
   `createApiFetch('/api/billing')`, `createApiFetch('/api')`, etc.
   respectively. Remove their duplicated fetch wrappers and auth-header
   logic. Their public API method signatures must remain unchanged.

4. Ensure `AuthContext.tsx` imports `setAuthToken` and `setMsalInstance`
   from `api.ts` (or update it to import from `fetchWrapper.ts` — either
   works as long as the re-export in step 2 is in place).

**Critical:** Do not change any component's imports or method call sites.
The public API of all four service files must remain identical. Only the
internal fetch mechanism changes.

---

## FIX 8: Add auth tests

### 8a. Frontend API client tests
**File to create:** `frontend/src/__tests__/api.test.ts`

Test the `fetchWrapper` / `api.ts` 401-refresh-retry behavior:
- Mock `global.fetch`.
- Mock the MSAL instance (`acquireTokenSilent` returns a response with
  `idToken`).
- Test: successful request on first try (no refresh needed).
- Test: 401 on first try -> silent refresh succeeds -> retry succeeds.
- Test: 401 on first try -> silent refresh fails -> `acquireTokenRedirect` is
  called (verify it was invoked).
- Test: non-401 error -> error message parsed from `detail` field.
- Test: headers include `Authorization: Bearer <token>` when token is set.
- Test: headers omit `Authorization` when no token is set.

Follow the existing test patterns in `frontend/src/__tests__/`. Check
`frontend/src/setupTests.ts` and `frontend/src/__tests__/AuthContext.test.tsx`
for how MSAL is mocked in this project.

### 8b. Backend auth tests
**File to create:** `backend/tests/test_auth.py`

Test `TokenVerifier` directly (not mocked):
- Test `_build_dev_user` returns the expected mock identity.
- Test `verify_token` rejects a malformed token (not a JWT) with HTTPException 401.
- Test `verify_token` rejects a token with no `kid` header.
- Test issuer validation: a token with a valid signature but wrong issuer
  is rejected. (You can use `python-jose` to sign a test JWT with a test
  RSA key — see existing test patterns in `backend/tests/` for how crypto
  is handled, or generate a test key pair with `cryptography`.)
- Test audience validation: a token signed with the right issuer but wrong
  audience is rejected.
- Test `get_current_user` returns dev user when `DEV_AUTH_BYPASS` is true.
- Test `get_current_user` raises 401 when no credentials are provided.

Look at `backend/tests/conftest.py` to understand the test setup
(`TESTING=true`, `DEV_AUTH_BYPASS` is forced false in tests, auth is mocked
for integration tests). Your `test_auth.py` should test the `TokenVerifier`
class in isolation, bypassing the conftest mock where needed.

---

## After all fixes: verify

1. Run backend tests:
   ```powershell
   cd backend
   $env:TESTING="true"; .venv\Scripts\python.exe -m pytest -v
   ```
   All existing tests must still pass, plus the new `test_auth.py` tests.

2. Run frontend tests:
   ```powershell
   cd frontend
   npm run test
   ```
   All existing tests must still pass, plus the new `api.test.ts` tests.

3. Run frontend build to confirm no TypeScript errors:
   ```powershell
   cd frontend
   npm run build
   ```

4. Run `terraform validate` from the `terraform/` directory to confirm
   Terraform changes are valid.

If any test fails, fix the issue and re-run until all pass. Do not leave
the codebase in a broken state.

## Constraints

- Do NOT change any component's imports or API method call signatures.
- Do NOT remove existing comments unless directly replacing them.
- Do NOT add emojis to code or comments.
- Follow existing code style in each file (indentation, quoting, etc.).
- Do NOT push, commit, or stage any changes. Leave all changes unstaged in
  the working tree for review. Do NOT run `git add`, `git commit`, or `git push`.
- If `python-jose` does not support list issuers (FIX 5), keep the manual
  verification with a clarifying comment — do not force a library upgrade.
