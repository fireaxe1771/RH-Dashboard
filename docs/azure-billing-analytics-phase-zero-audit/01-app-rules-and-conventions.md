# Supporting Doc 01 — Application Rules and Conventions

**Project:** RecoveryHub Dashboard System  
**Purpose:** Authoritative reference for all coding conventions, architectural patterns, and rules that every new file in this repository must follow.

> **This document must be read before writing a single line of code.** All billing integration code must be consistent with the patterns established in the existing backend and frontend. Inconsistency causes maintenance burden and will require rework.

---

## 1. Python Backend Rules

### 1.1 Python Version

- **Python 3.11** is the target runtime (pinned in `Dockerfile`: `python:3.11-slim-bookworm`)
- Use **`str | None`** union syntax, not `Optional[str]` from `typing`
- Use **`list[str]`**, **`dict[str, Any]`**, not `List[str]`, `Dict[str, Any]`
- `from __future__ import annotations` is not used in this codebase — do not add it

### 1.2 Project Structure

- All backend Python files live directly in `backend/` or in a named subpackage (e.g., `backend/billing/`)
- Every subpackage requires an `__init__.py` (can be empty)
- Test files live exclusively in `backend/tests/` and are named `test_<module>.py`
- No business logic in `main.py` beyond routing and lifespan — delegate to service modules

### 1.3 Configuration and Environment Variables

- **All configuration is managed through `config.py`'s `Settings` class.** Never call `os.getenv()` outside of `config.py`.
- The `Settings` class holds all configuration as class-level attributes with typed defaults.
- `settings = Settings()` is the module-level singleton imported everywhere: `from config import settings`
- **Fail loudly at startup.** `validate_settings()` raises `ValueError` with a clear error message listing every missing variable. The app must not start in a misconfigured state.
- The billing extension adds `validate_billing_settings()` to `Settings`, called conditionally when `BILLING_SYNC_ENABLED=true`.
- `TESTING=true` env var disables startup validation (used by pytest via conftest fixtures).

### 1.4 Async/Sync Discipline

- **MongoDB operations are always `async def`** using Motor's async API.
- **FastAPI route functions are `async def`** unless they call blocking I/O, in which case they are `def` and FastAPI dispatches them to a thread pool automatically.
- **Azure SDK calls (azure-identity, azure-mgmt-*)** are synchronous by default. Wrap them with `asyncio.get_event_loop().run_in_executor(None, sync_fn)` or use the `async` variants of the SDKs (`aio` subpackage of `azure-mgmt-*`) where available.
- **aiohttp** is used for all custom async HTTP calls (Retail Prices API, polling loops).
- Never call `asyncio.run()` inside an async context.
- Never use `time.sleep()` inside async functions — use `await asyncio.sleep()`.

### 1.5 Pydantic Models

- **Pydantic v2** (`pydantic==2.7.4`) is the data validation library.
- Every API request body has a `BaseModel` in `models.py` (or `billing_models.py` for billing).
- Every API response body has a `BaseModel`.
- Internal data structures passed between functions use `BaseModel` or typed `TypedDict` where Pydantic overhead is unnecessary.
- Use `Field(...)` for required fields and `Field(None)` for optional fields.
- Use `Field(default=..., description="...")` — always provide descriptions on public-facing models.
- `class Config: populate_by_name = True` is required on models with `alias` fields.

### 1.6 Error Handling

- **Custom exceptions** for each domain: `BillingAPIError` for Azure API failures, `VectorizerError` for embedding failures. Inherit from `Exception`.
- FastAPI HTTP exceptions: `raise HTTPException(status_code=..., detail="clear message")`.
- **Never catch `Exception` broadly and silently continue.** Either re-raise, log and re-raise, or convert to an `HTTPException`.
- Log the full exception with `logger.error(f"...: {e}")` before raising.
- Use `logger = logging.getLogger(__name__)` in every module — never use `print()` for logging.

### 1.7 Logging

- All modules use `logger = logging.getLogger(__name__)`.
- Logging is configured globally in `main.py`: `logging.basicConfig(level=logging.INFO)`.
- Log levels: `INFO` for normal operations (startup, sync complete, API calls), `WARNING` for recoverable issues (retry, fallback), `ERROR` for failures that affect functionality.
- Do not log sensitive values (tokens, passwords, client secrets).

### 1.8 Database Patterns

- MongoDB client is the `DatabaseManager` singleton in `database.py`.
- `get_db()` is the FastAPI dependency function that yields the active Motor database object.
- All route functions that need MongoDB use `db = Depends(get_db)`.
- All write operations use `upsert=True` with a meaningful filter for idempotency (never blind inserts for sync data).
- Index creation happens in `init_indexes()` in `database.py` — never in application code or route handlers.
- Collection names are `snake_case` and follow the pattern `azure_billing_*` for billing data.
- MongoDB document `_id` fields are always serialized to `str` before returning in API responses.

### 1.9 Code Style

- **No trailing whitespace.**
- **4-space indentation** (no tabs).
- **Line length**: soft 100 characters, hard 120 characters.
- **No unused imports.** Remove any import not referenced in the file.
- **Comments**: Do not add or remove comments unnecessarily. Add comments only for non-obvious logic. Preserve all existing comments exactly.
- **Docstrings**: Public classes and public functions have docstrings. One-line docstrings for simple functions. Multi-line for complex ones using the existing style: `"""Short summary.\n\nDetail paragraph if needed."""`
- **Type annotations**: All function parameters and return types are annotated. Use `-> None` for functions that don't return a value.

### 1.10 Security Rules

- **No hardcoded credentials, keys, IDs, or tokens anywhere in the codebase.**
- All secrets flow through `config.py` → environment variables.
- SQL queries are always parameterized — never use string formatting or f-strings for user-supplied values in SQL.
- Billing API calls use bearer tokens — never log the full token value, only a prefix for debugging.
- `AZURE_BILLING_CLIENT_SECRET` and `OPENAI_API_KEY` are flagged `sensitive=true` in Terraform.

---

## 2. Testing Rules

### 2.1 Framework

- **pytest** with **pytest-asyncio** for async tests.
- **mongomock** for mocking MongoDB in tests (already used in existing tests via `conftest.py`).
- **httpx** for testing FastAPI endpoints (already used).
- `unittest.mock.AsyncMock` and `unittest.mock.patch` for mocking async functions.

### 2.2 Test Requirements

- Every new module under `backend/billing/` **must** have a corresponding test file under `backend/tests/test_billing_*.py`.
- Every new API route must be tested with at least: a happy path test, an auth-required test (no token → 401), and an error case test.
- All external dependencies must be mocked: Azure SDK calls, OpenAI API calls, aiohttp requests, MongoDB (use mongomock fixtures from conftest).
- Tests must not make real network calls. Use `@patch` or `pytest-mock` to intercept all outbound HTTP.

### 2.3 Existing Conftest Fixtures

The existing `backend/tests/conftest.py` provides:
- `mock_db` — a mongomock async database fixture
- `test_client` — an httpx `AsyncClient` with app dependency overrides
- `mock_user_token` — a pre-built valid JWT payload for auth bypass in tests
- `mock_sql_connection` — mocked Azure SQL connection

Billing tests must extend `conftest.py` with:
- `mock_billing_credential` — a `MagicMock` of `ClientSecretCredential`
- `mock_azure_token` — a fake token string returned by the mocked credential
- `mock_openai_client` — a `MagicMock` of `openai.OpenAI` with pre-configured embedding responses

### 2.4 Test Execution

```bash
cd backend
pytest -v                    # Run all tests
pytest -v tests/test_billing_cost_management.py  # Run specific test file
pytest -v --tb=short         # Short traceback format
```

All tests must pass before any commit to `main`.

---

## 3. Frontend Rules

### 3.1 Framework and Language

- **React 18.3.1** with **TypeScript 5.2.2**.
- **Functional components only** — no class components.
- **React hooks**: `useState`, `useEffect`, `useCallback`, `useMemo`. No custom hook libraries.
- No external state management libraries (no Redux, Zustand, etc.).

### 3.2 Component Conventions

- Component files are **PascalCase**: `BillingOverview.tsx`.
- Each component is in its own file.
- Component props are defined as a TypeScript `interface` immediately above the component function.
- Loading states use a `isLoading: boolean` state variable and render a simple loading indicator.
- Error states use an `error: string | null` state variable and render an error message.
- Empty/no-data states always display a meaningful message, never a blank panel.

### 3.3 Styling

- **No separate CSS files for components.** All styles use React inline style objects.
- Style objects follow the same pattern as existing components (see `WidgetCard.tsx`, `Sidebar.tsx`).
- Color palette uses CSS variables defined in `index.css`:
  - `--color-bg-primary`, `--color-bg-secondary`, `--color-bg-card`
  - `--color-text-primary`, `--color-text-secondary`, `--color-text-muted`
  - `--color-accent-blue`, `--color-accent-green`, `--color-accent-red`, `--color-accent-yellow`
  - `--color-border`
- Use **`lucide-react`** for all icons (already a dependency). Import individually: `import { DollarSign } from 'lucide-react'`.
- No other icon libraries.

### 3.4 API Calls

- All API calls are in `services/billingApi.ts`, not inline in components.
- Use `fetch()` with the `Authorization: Bearer <token>` header. Token is obtained from `AuthContext`.
- All response types are fully typed TypeScript interfaces defined at the top of `billingApi.ts`.
- Errors are handled by the API service functions: on non-2xx response, throw a `new Error(detail)` where `detail` is the `response.json().detail` field.
- Follow the exact pattern of `services/api.ts` for structure and error handling.

### 3.5 TypeScript Rules

- **Strict mode is on.** No use of `any` types unless absolutely necessary and annotated with a comment.
- All API response types have corresponding TypeScript interfaces.
- No `// @ts-ignore` or `// @ts-nocheck` comments.
- All props interfaces use `readonly` for non-callback props.

### 3.6 Testing

- **Vitest** with **React Testing Library** and **jsdom**.
- Test files are in `frontend/src/__tests__/`.
- New billing components must have basic render tests.

```bash
cd frontend
npm run test     # Run all tests
npm run build    # Must pass TypeScript type checking and build
```

---

## 4. Git and Commit Rules

- **Never commit to `main` directly.** All changes go through a feature branch and PR.
- **Branch naming**: `feat/billing-phase-1`, `feat/billing-auth`, etc.
- **Commit messages**: imperative mood, reference the phase (e.g., `feat: add billing auth module (Phase 2)`).
- **Never commit**: `.env`, secrets, generated files, `node_modules/`, `__pycache__/`, `.venv/`.
- Run tests before committing — the CI/CD pipeline will reject failing tests.

---

## 5. Docker and Container Rules

- The backend `Dockerfile` is `python:3.11-slim-bookworm` base. Do not change the base image.
- The Dockerfile installs Microsoft ODBC Driver 18 via the Microsoft Debian 12 package repo — this is required for Azure AD SQL auth and must not be removed.
- New Python packages added to `requirements.txt` must not require additional system-level packages that aren't already in the Dockerfile. If they do, update the `apt-get install` block in the Dockerfile accordingly.
- `apscheduler`, `openai`, `aiohttp`, `aiofiles`, and the `azure-mgmt-*` packages all install cleanly without additional system dependencies.

---

## 6. Existing Code Patterns to Mirror

### 6.1 Module-Level Singleton Pattern

Existing example from `database.py`:
```python
# Single instance of DB Manager for the backend application
db_manager = DatabaseManager()

def get_db():
    """Dependency provider yielding active MongoDB instance."""
    if db_manager.db is None:
        raise RuntimeError("Database connection is offline.")
    return db_manager.db
```

Apply the same pattern to `billing/auth.py` — a module-level credential singleton.

### 6.2 Startup Validation Pattern

Existing example from `config.py`:
```python
if missing:
    error_msg = (
        f"\nFATAL CONFIGURATION ERROR: The following required environment variables are missing:\n"
        f"{', '.join(missing)}\n"
        f"Please define them in your .env file or Azure Container App settings.\n"
        f"Halting server boot.\n"
    )
    sys.stderr.write(error_msg)
    sys.stderr.flush()
    raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
```

New billing validation must use the same format, the same stderr write pattern, and the same `ValueError` raise.

### 6.3 Lifespan Pattern

Existing example from `main.py`:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        db_manager.connect()
        await db_manager.init_indexes()
        await _seed_default_dashboards()
    except Exception as e:
        logger.critical(f"Database Initialization Failed during startup: {e}")
        raise e
    
    yield
    
    db_manager.disconnect()
```

The billing scheduler start/stop follows the same pattern — in `try` before `yield`, cleanup after `yield`.

### 6.4 Route Handler Pattern

Existing example from `main.py`:
```python
@app.get("/api/dashboards/{dashboard_id}", response_model=DashboardResponse)
async def get_dashboard(dashboard_id: str, db=Depends(get_db), current_user: dict = Depends(get_current_user)):
    """Fetches a single dashboard definition by its MongoDB ObjectId."""
    try:
        obj_id = ObjectId(dashboard_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid dashboard ID format.")
    
    dashboard = await db["dashboards"].find_one({"_id": obj_id})
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found.")
    return serialize_mongo_doc(dashboard)
```

All new billing routes must follow: `async def`, `Depends(get_db)`, `Depends(get_current_user)`, try/except, `HTTPException` on error, return typed Pydantic response model.

### 6.5 Token Caching Pattern

Existing example from `target_db.py`:
```python
_TOKEN_REFRESH_MARGIN = 300  # seconds

def _get_access_token(self) -> str:
    with self._token_lock:
        if self._cached_token and time.time() < (self._token_expiry - self._TOKEN_REFRESH_MARGIN):
            return self._cached_token
        # ... acquire new token
        self._cached_token = token
        self._token_expiry = expiry
        return self._cached_token
```

The `azure.identity.ClientSecretCredential` handles this automatically. Do not re-implement manual token caching for the billing credential — use `credential.get_token()` directly and let the SDK cache it.

---

## 7. Naming Conventions Summary

| Category | Convention | Example |
|---|---|---|
| Python files | `snake_case.py` | `sync_service.py` |
| Python packages | `snake_case/` | `billing/` |
| Python classes | `PascalCase` | `BillingAPIError` |
| Python functions | `snake_case` | `sync_cost_details()` |
| Python constants | `UPPER_SNAKE_CASE` | `TOKEN_REFRESH_MARGIN` |
| MongoDB collections | `snake_case` with `azure_` prefix | `azure_cost_details` |
| MongoDB fields | `snake_case` | `billing_period`, `pre_tax_cost` |
| Env variable names | `UPPER_SNAKE_CASE` | `AZURE_BILLING_CLIENT_ID` |
| React components | `PascalCase.tsx` | `BillingOverview.tsx` |
| React component dirs | `PascalCase` or `snake_case` | `billing/` |
| TypeScript interfaces | `PascalCase` with `I` prefix optional | `CostSummary`, `BillingAlert` |
| TypeScript functions | `camelCase` | `getCostSummary()` |
| API routes | `kebab-case` | `/api/billing/cost/top-spenders` |
| Terraform resources | `snake_case` | `azurerm_container_app.backend` |
| Terraform variables | `snake_case` | `azure_billing_client_secret` |
