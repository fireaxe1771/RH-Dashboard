import pymssql
import logging
import re
import struct
import threading
import time
from datetime import date, timedelta
from typing import List, Dict, Any, Tuple
from azure.identity import ClientSecretCredential
from config import settings
from models import DashboardFilters

try:
    import pyodbc
except ImportError:  # pragma: no cover - runtime dependency may not be installed in tests
    pyodbc = None

logger = logging.getLogger(__name__)

class TargetDatabaseError(Exception):
    """Base exception for errors connecting to or querying target Azure SQL."""
    pass

class QueryValidationError(TargetDatabaseError):
    """Exception raised when a user-defined SQL query fails read-only security checks."""
    pass

class SQLConnection:
    """Manages raw connections and query execution against the target Azure SQL Database."""

    # Azure AD tokens are valid for ~60-75 minutes.  We cache the token and
    # refresh it 5 minutes before expiry so that concurrent widget requests
    # share a single token instead of each acquiring their own (~0.9 s each).
    _TOKEN_REFRESH_MARGIN = 300  # seconds

    def __init__(self):
        self._claims_date_column: str | None = None
        self._claims_column_map: Dict[str, str] | None = None
        self._cached_token: str | None = None
        self._token_expiry: float = 0.0  # epoch seconds
        self._token_lock = threading.Lock()

    def _get_odbc_connection(self, access_token: str):
        """Connect to Azure SQL using an Azure AD access token via pyodbc."""
        if pyodbc is None:
            raise TargetDatabaseError(
                "pyodbc is required for Azure AD SQL authentication. Install it and ensure an ODBC Driver for SQL Server is available."
            )

        driver = "ODBC Driver 18 for SQL Server"
        connection_string = (
            f"DRIVER={{{driver}}};"
            f"SERVER=tcp:{settings.AZURE_SQL_HOST},{settings.AZURE_SQL_PORT};"
            f"DATABASE={settings.AZURE_SQL_DB};"
            "Encrypt=yes;"
            "TrustServerCertificate=no;"
            "Connection Timeout=15;"
        )

        access_token_bytes = access_token.encode("utf-16-le")
        token_struct = struct.pack(f"=I{len(access_token_bytes)}s", len(access_token_bytes), access_token_bytes)
        return pyodbc.connect(connection_string, attrs_before={1256: token_struct})

    @staticmethod
    def _rows_to_dicts(cursor, rows):
        columns = [desc[0] for desc in cursor.description]
        cleaned_rows = []
        for row in rows:
            cleaned_row = {}
            for idx, col in enumerate(columns):
                val = row[idx]
                if hasattr(val, 'isoformat'):
                    cleaned_row[col] = val.isoformat()
                elif val.__class__.__name__ == 'Decimal':
                    cleaned_row[col] = float(val)
                else:
                    cleaned_row[col] = val
            cleaned_rows.append(cleaned_row)
        return columns, cleaned_rows

    @staticmethod
    def _execute_query(cursor, query: str, params: Dict[str, Any]):
        """Execute a query with either pymssql named parameters or pyodbc positional parameters."""
        if pyodbc is not None and isinstance(cursor, pyodbc.Cursor):
            named_pattern = re.compile(r"%\(([^)]+)\)s")
            param_names = named_pattern.findall(query)
            if param_names:
                positional_query = named_pattern.sub("?", query)
                values = [params[name] for name in param_names]
                return cursor.execute(positional_query, values)
            return cursor.execute(query)

        return cursor.execute(query, params)

    def _resolve_claims_date_column(self, conn) -> str:
        """Find the best available Claims date column, preferring the temporal row-start column."""
        if self._claims_date_column:
            return self._claims_date_column

        metadata_query = """
        SELECT TOP 1 c.name
        FROM sys.tables t
        INNER JOIN sys.columns c ON c.object_id = t.object_id
        WHERE t.name = 'Claims'
          AND c.is_hidden IN (0, 1)
          AND c.system_type_id IN (40, 41, 42, 43, 58, 61)
        ORDER BY
            CASE
                WHEN c.generated_always_type_desc = 'AS_ROW_START' THEN 0
                WHEN c.name IN ('DateCreated', 'CreatedDate', 'CreatedAt', 'CreatedOn', 'InsertDate', 'InsertedAt', 'InsertedOn') THEN 1
                WHEN c.name LIKE '%Date%' OR c.name LIKE '%Time%' THEN 2
                ELSE 3
            END,
            c.column_id
        """

        try:
            with conn.cursor() as cursor:
                cursor.execute(metadata_query)
                row = cursor.fetchone()
        except Exception as e:
            logger.warning(f"Failed to resolve Claims date column from metadata: {e}")
            return "DateCreated"

        if row and row[0]:
            self._claims_date_column = row[0]
        else:
            self._claims_date_column = "DateCreated"

        return self._claims_date_column

    @staticmethod
    def _rewrite_claims_date_column(query: str, date_column: str) -> str:
        """Rewrites legacy DateCreated references to the resolved Claims date column."""
        if date_column == "DateCreated" or not re.search(r"\bDateCreated\b", query, re.IGNORECASE):
            return query

        return re.sub(r"\bDateCreated\b", date_column, query)

    # SQL keywords excluded when detecting table aliases after FROM Claims
    _ALIAS_EXCLUDE = frozenset({
        'WHERE', 'JOIN', 'INNER', 'LEFT', 'RIGHT', 'FULL', 'CROSS', 'ON',
        'GROUP', 'ORDER', 'HAVING', 'UNION', 'EXCEPT', 'INTERSECT', 'AND',
        'OR', 'NOT', 'SET', 'INTO', 'VALUES', 'FROM', 'SELECT', 'WITH',
        'WHEN', 'THEN', 'ELSE', 'END', 'AS', 'CASE', 'TOP', 'DISTINCT',
        'ALL', 'EXISTS', 'IN', 'BETWEEN', 'LIKE', 'IS', 'NULL', 'DESC',
        'ASC', 'LIMIT', 'OFFSET', 'FETCH', 'FOR', 'INSERT', 'UPDATE',
        'DELETE', 'DROP', 'ALTER', 'CREATE', 'TRUNCATE', 'EXEC', 'EXECUTE',
    })

    def _inject_claim_filters(self, query: str, filters: DashboardFilters,
                               column_map: Dict[str, str]) -> str:
        """Auto-inject department/processor filter conditions into Claims queries.

        Wraps ``FROM Claims`` references with a filtered subquery so that
        department and processor filters are applied even when the widget SQL
        does not explicitly include filter parameters.

        Date filters (start_date / end_date) are NOT auto-injected — widgets
        that need date filtering include %(start_date)s / %(end_date)s
        explicitly so YTD widgets are not unintentionally narrowed.
        """
        if not filters:
            return query
        if not re.search(r'\bClaims\b', query, re.IGNORECASE):
            return query

        dept_col = column_map.get('DepartmentID', 'DepartmentID')
        proc_col = column_map.get('ProcessorID', 'ProcessorID')

        conditions: list[str] = []
        if filters.department_id and '%(department_id)s' not in query:
            conditions.append(f"{dept_col} = %(department_id)s")
        if filters.processor_id and '%(processor_id)s' not in query:
            conditions.append(f"{proc_col} = %(processor_id)s")

        if not conditions:
            return query

        where_clause = ' AND '.join(conditions)

        # Pattern: FROM Claims [FOR SYSTEM_TIME {ALL | BETWEEN ... AND ...}] [alias]
        pattern = (
            r'\bFROM\s+Claims\b'
            r'(\s+FOR\s+SYSTEM_TIME\s+(?:ALL|BETWEEN\s+[^\s]+\s+AND\s+[^\s]+)\b)?'
            r'(?:\s+(?!'
            + '|'.join(rf'{kw}\b' for kw in sorted(self._ALIAS_EXCLUDE))
            + r')([a-zA-Z_]\w*))?'
        )

        def _replace(match: re.Match) -> str:
            temporal = (match.group(1) or '').strip()
            alias = match.group(2) or '_fc'
            temporal_str = f' {temporal}' if temporal else ''
            inner = f'SELECT * FROM Claims{temporal_str} WHERE {where_clause}'
            return f'FROM ({inner}) {alias}'

        return re.sub(pattern, _replace, query, flags=re.IGNORECASE)

    def get_server_date(self) -> date:
        """Return the current date from SQL Server via ``CAST(GETDATE() AS DATE)``."""
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT CAST(GETDATE() AS DATE)")
                row = cursor.fetchone()
                if row and row[0]:
                    val = row[0]
                    if isinstance(val, date):
                        return val
                    return date.fromisoformat(str(val))
        except Exception as e:
            logger.warning(f"Failed to fetch server date, falling back to UTC: {e}")
        finally:
            conn.close()
        return date.today()

    @staticmethod
    def compute_date_range(server_today: date, range_type: str, periods_back: int
                           ) -> tuple[str, str]:
        """Compute a (start, end) date range based on *server_today*.

        Week boundaries are **Sunday → Saturday**.
        For the current period (periods_back == 0), end_date is today.
        """
        if range_type == 'week':
            dow = server_today.weekday()  # Mon=0 … Sun=6
            # days since last Sunday: Sunday weekday()=6 → 0, Mon=0 → 1, … Sat=5 → 6
            days_since_sunday = (dow + 1) % 7
            sunday = server_today - timedelta(days=days_since_sunday + periods_back * 7)
            saturday = sunday + timedelta(days=6)
            end = server_today if periods_back == 0 else saturday
            return sunday.isoformat(), end.isoformat()

        if range_type == 'month':
            # Walk back *periods_back* months from the 1st of the current month
            ref_year = server_today.year
            ref_month = server_today.month - periods_back
            while ref_month < 1:
                ref_month += 12
                ref_year -= 1
            start = date(ref_year, ref_month, 1)
            # Last day of the month
            next_month = ref_month + 1
            next_year = ref_year
            if next_month > 12:
                next_month = 1
                next_year += 1
            last_day = date(next_year, next_month, 1) - timedelta(days=1)
            end = server_today if periods_back == 0 else last_day
            return start.isoformat(), end.isoformat()

        if range_type == 'year':
            yr = server_today.year - periods_back
            start = date(yr, 1, 1)
            end = server_today if periods_back == 0 else date(yr, 12, 31)
            return start.isoformat(), end.isoformat()

        # 'day' or unknown — return today
        return server_today.isoformat(), server_today.isoformat()

    @staticmethod
    def compute_prior_period(start_str: str | None, end_str: str | None):
        """Compute an equally-long prior period ending the day before *start_str*."""
        if not start_str or not end_str:
            return None, None
        try:
            start = date.fromisoformat(start_str)
            end = date.fromisoformat(end_str)
        except (ValueError, TypeError):
            return None, None
        period_days = (end - start).days
        if period_days < 0:
            return None, None
        prior_end = start - timedelta(days=1)
        prior_start = prior_end - timedelta(days=period_days)
        return prior_start.isoformat(), prior_end.isoformat()

    def _prepare_claims_query(self, query: str, conn,
                               filters: DashboardFilters | None = None) -> str:
        """Normalizes legacy dashboard SQL before validation/execution."""
        if not re.search(r"\bClaims\b", query, re.IGNORECASE):
            return query

        claims_column_map = self._resolve_claims_column_map(conn)
        for legacy_name, actual_name in claims_column_map.items():
            if legacy_name == actual_name:
                continue
            query = re.sub(rf"\b{re.escape(legacy_name)}\b", actual_name, query)

        claims_date_column = self._resolve_claims_date_column(conn)
        query = self._rewrite_claims_date_column(query, claims_date_column)

        # Auto-inject department / processor filters
        if filters:
            query = self._inject_claim_filters(query, filters, claims_column_map)

        return query

    def _resolve_claims_column_map(self, conn) -> Dict[str, str]:
        """Builds a legacy-to-actual column map for the Claims table from live metadata."""
        if self._claims_column_map:
            return self._claims_column_map

        metadata_query = """
        SELECT c.name
        FROM sys.tables t
        INNER JOIN sys.columns c ON c.object_id = t.object_id
        WHERE t.name = 'Claims'
        ORDER BY c.column_id
        """

        try:
            with conn.cursor() as cursor:
                cursor.execute(metadata_query)
                rows = cursor.fetchall()
        except Exception as e:
            logger.warning(f"Failed to resolve Claims columns from metadata: {e}")
            self._claims_column_map = {
                "ClaimID": "ClaimID",
                "DateCreated": "DateCreated",
            }
            return self._claims_column_map

        available_columns = [str(row[0]) for row in rows if row and row[0]]
        available_lookup = {name.lower(): name for name in available_columns}

        def pick(*candidates: str) -> str | None:
            for candidate in candidates:
                match = available_lookup.get(candidate.lower())
                if match:
                    return match
            return None

        # Prefer stable identifier/date columns and then common field names used by the dashboard.
        claims_id = pick(
            "ClaimID", "ClaimId", "ClaimNumber", "RunNumber", "RunID", "RunId", "ID", "Id", "ClaimKey"
        )
        claims_date = pick(
            "DateCreated", "CreatedDate", "CreatedAt", "CreatedOn", "SysStartTime", "ValidFrom", "StartTime"
        )

        column_map = {
            "ClaimID": claims_id or "ClaimID",
            "DateCreated": claims_date or "DateCreated",
            "submitted": pick("submitted", "Submitted", "IsSubmitted", "SubmittedFlag", "HasSubmitted") or "submitted",
            "original_run_id": pick(
                "original_run_id", "OriginalRunId", "OriginalRunID", "RunID", "RunId", "ParentRunID", "SourceRunID"
            ) or "original_run_id",
            "archived": pick("archived", "Archived", "IsArchived") or "archived",
            "user_id": pick("user_id", "UserID", "UserId", "AssignedUserID", "OwnerUserID", "ProcessorUserID") or "user_id",
            "Status": pick("Status", "ClaimStatus", "CurrentStatus") or "Status",
            "DepartmentID": pick("DepartmentID", "DepartmentId") or "DepartmentID",
            "DepartmentName": pick("DepartmentName", "Department", "DeptName") or "DepartmentName",
            "ProcessorID": pick("ProcessorID", "ProcessorId") or "ProcessorID",
            "ProcessorName": pick("ProcessorName", "Processor", "AssignedToName") or "ProcessorName",
            "ClaimType": pick("ClaimType", "ClaimTypeName") or "ClaimType",
            "Amount": pick("Amount", "TotalAmount", "ClaimAmount") or "Amount",
            "RunNumber": pick("RunNumber", "RunNo", "RunNbr") or "RunNumber",
        }

        self._claims_column_map = column_map
        return self._claims_column_map

    def _get_azure_ad_token(self) -> str:
        """Return a cached Azure AD access token, refreshing only when near expiry.

        Thread-safe: concurrent callers block briefly on the lock while one
        thread refreshes, then all share the new token.
        """
        now = time.time()
        if self._cached_token and now < self._token_expiry - self._TOKEN_REFRESH_MARGIN:
            return self._cached_token

        with self._token_lock:
            # Double-check after acquiring the lock (another thread may have refreshed)
            now = time.time()
            if self._cached_token and now < self._token_expiry - self._TOKEN_REFRESH_MARGIN:
                return self._cached_token

            credential = ClientSecretCredential(
                tenant_id=settings.AZURE_SQL_TENANT_ID,
                client_id=settings.AZURE_SQL_USER,
                client_secret=settings.AZURE_SQL_PASSWORD,
            )
            token = credential.get_token("https://database.windows.net/.default")
            self._cached_token = token.token
            self._token_expiry = token.expires_on  # epoch seconds
            logger.info("Azure AD token refreshed (expires %s)", self._token_expiry)
            return self._cached_token

    def _get_connection(self):
        """Establishes a connection using parameters from configuration settings."""
        try:
            if settings.AZURE_SQL_AUTHENTICATION == "azure-ad":
                access_token = self._get_azure_ad_token()
                return self._get_odbc_connection(access_token)
            else:
                # Basic authentication using username/password
                return pymssql.connect(
                    server=settings.AZURE_SQL_HOST,
                    port=settings.AZURE_SQL_PORT,
                    database=settings.AZURE_SQL_DB,
                    user=settings.AZURE_SQL_USER,
                    password=settings.AZURE_SQL_PASSWORD,
                    login_timeout=5,  # Fail quickly if database is offline (fail loudly)
                    timeout=15
                )
        except Exception as e:
            logger.error(f"Failed to connect to Azure SQL Database at {settings.AZURE_SQL_HOST}: {e}")
            raise TargetDatabaseError(f"Azure SQL Connection Failure: {e}")

    def validate_query(self, query: str) -> None:
        """Validates that query is strictly read-only and safe from destructive SQL keywords."""
        cleaned_query = query.strip()
        
        # Enforce that query must start with SELECT (ignoring leading whitespace/comments)
        # Strip comments
        query_no_comments = re.sub(r'(--.*?$)|(/\*.*?\*/)', '', cleaned_query, flags=re.MULTILINE)
        query_stripped = re.sub(r'^[;\s]+', '', query_no_comments)
        
        if not query_stripped.upper().startswith(("SELECT", "WITH")):
            raise QueryValidationError("Query Security Breach: Only read-only 'SELECT' statements are permitted.")
        
        # Block destructive SQL command keywords
        destructive_patterns = [
            r"\bINSERT\b", r"\bUPDATE\b", r"\bDELETE\b", r"\bDROP\b",
            r"\bALTER\b", r"\bCREATE\b", r"\bTRUNCATE\b", r"\bEXEC\b",
            r"\bEXECUTE\b", r"\bGRANT\b", r"\bREVOKE\b",
            r"\bSELECT\b[^;]*?\bINTO\b",  # SELECT ... INTO (table creation)
        ]
        
        for pattern in destructive_patterns:
            if re.search(pattern, query_stripped, re.IGNORECASE):
                cleaned_pattern = pattern.strip(r' \b')
                raise QueryValidationError(f"Query Security Breach: Prohibited keyword detected: '{cleaned_pattern}'")

    def execute_read(self, query: str, filters: DashboardFilters = None) -> Dict[str, Any]:
        """Executes a SQL query with parameter bindings, returning columns and row values.
        
        When *filters* carries ``range_type`` and ``periods_back``, the date
        range is recomputed from SQL Server’s ``GETDATE()`` so every widget
        uses the **database server clock** instead of the browser’s local time.
        """
        # --- resolve dates from the database server when range_type is set ---
        start_date = filters.start_date if filters else None
        end_date = filters.end_date if filters else None

        if filters and filters.range_type and filters.periods_back is not None:
            try:
                server_today = self.get_server_date()
                start_date, end_date = self.compute_date_range(
                    server_today, filters.range_type, filters.periods_back,
                )
            except Exception as e:
                logger.warning(f"Server-date resolution failed, using filter dates: {e}")

        prior_start, prior_end = self.compute_prior_period(start_date, end_date)

        # Compute YTD start (Jan 1 of the year containing the end_date)
        ytd_start = None
        if end_date:
            try:
                from datetime import date as _date
                ed = _date.fromisoformat(str(end_date))
                ytd_start = f"{ed.year}-01-01"
            except (ValueError, TypeError):
                pass

        params = {
            "department_id": filters.department_id if filters else None,
            "processor_id": filters.processor_id if filters else None,
            "start_date": start_date,
            "end_date": end_date,
            "prior_start_date": prior_start,
            "prior_end_date": prior_end,
            "ytd_start": ytd_start,
        }

        try:
            conn = self._get_connection()
        except Exception as e:
            logger.error(f"Azure SQL connection error: {e}")
            raise TargetDatabaseError(f"Azure SQL Connection Failure: {e}")

        try:
            query = self._prepare_claims_query(query, conn, filters)
            self.validate_query(query)

            with conn.cursor() as cursor:
                self._execute_query(cursor, query, params)
                rows = cursor.fetchall()
                
                # If query returned nothing (e.g. valid select with empty results)
                if cursor.description is None:
                    return {"columns": [], "rows": []}

                columns, cleaned_rows = self._rows_to_dicts(cursor, rows)

                return {
                    "columns": columns,
                    "rows": cleaned_rows
                }
        except TargetDatabaseError:
            raise
        except Exception as e:
            logger.error(f"SQL execution error on query: '{query}': {e}")
            raise TargetDatabaseError(f"Database Query Execution Failed: {e}")
        finally:
            conn.close()

    def get_db_schema(self) -> List[Dict[str, Any]]:
        """Scans INFORMATION_SCHEMA.COLUMNS to build database structural tree.
        
        Fails loudly if schema catalog is unreachable.
        """
        schema_query = """
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE 
        FROM INFORMATION_SCHEMA.COLUMNS 
        WHERE TABLE_SCHEMA = 'dbo' 
        ORDER BY TABLE_NAME, ORDINAL_POSITION
        """
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(schema_query)
                rows = cursor.fetchall()
                
                # Group columns by table names
                tables_map: Dict[str, List[Dict[str, str]]] = {}
                for row in rows:
                    tbl = row[0]
                    col = row[1]
                    dtype = row[2]
                    
                    if tbl not in tables_map:
                        tables_map[tbl] = []
                    tables_map[tbl].append({"name": col, "type": dtype})
                
                return [
                    {"table": name, "columns": cols}
                    for name, cols in tables_map.items()
                ]
        except Exception as e:
            logger.error(f"Failed to fetch database schema metadata: {e}")
            raise TargetDatabaseError(f"Failed to scan target SQL Schema Catalog: {e}")
        finally:
            conn.close()

    def get_filter_dropdown_options(self) -> Dict[str, Any]:
        """Loads filter options from target DB, fall-backing to active claims distinct lookups if master tables are absent."""
        conn = self._get_connection()
        departments = []
        processors = []
        
        try:
            with conn.cursor() as cursor:
                # 1. Fetch departments. Try master table first, fallback to transactions
                try:
                    cursor.execute("SELECT ID as id, Name as name FROM Departments ORDER BY Name")
                    departments = cursor.fetchall()
                except Exception:
                    # Fallback lookup in claims
                    try:
                        cursor.execute("SELECT DISTINCT DepartmentID as id, DepartmentName as name FROM Claims WHERE DepartmentID IS NOT NULL ORDER BY DepartmentName")
                        departments = cursor.fetchall()
                    except Exception:
                        logger.warning("Neither Departments table nor Claims.DepartmentID columns found.")
                        departments = []

                # 2. Fetch claims processors
                try:
                    cursor.execute("SELECT ID as id, Name as name FROM Processors ORDER BY Name")
                    processors = cursor.fetchall()
                except Exception:
                    try:
                        cursor.execute("SELECT DISTINCT ProcessorID as id, ProcessorName as name FROM Claims WHERE ProcessorID IS NOT NULL ORDER BY ProcessorName")
                        processors = cursor.fetchall()
                    except Exception:
                        logger.warning("Neither Processors table nor Claims.ProcessorID columns found.")
                        processors = []

                return {
                    "departments": [{"id": str(d[0]), "name": d[1]} for d in departments if d[0] is not None],
                    "processors": [{"id": str(p[0]), "name": p[1]} for p in processors if p[0] is not None],
                    "claimTypes": []
                }
        except Exception as e:
            logger.error(f"Fatal error fetching filter options: {e}")
            raise TargetDatabaseError(f"Failed to load filter choices from target DB: {e}")
        finally:
            conn.close()

    def execute_drilldown(self, field_name: str, field_value: Any, filters: DashboardFilters = None) -> Dict[str, Any]:
        """Constructs and executes a parameterized search of detailed claims records for visual drill-downs."""
        # Enforce column keys sanitization (only alphanumeric/underscore allowed for column identifiers)
        if not re.match(r"^[a-zA-Z0-9_]+$", field_name):
            raise QueryValidationError("Invalid column name token provided for drill-down.")

        # Establish base query
        # We assume standard column identifiers that match transaction records (Claims table)
        base_query = """
        SELECT TOP 100
            ClaimID, 
            RunNumber, 
            DepartmentName, 
            ClaimType, 
            Status, 
            Amount, 
            ProcessorName, 
            DateCreated 
        FROM Claims 
        WHERE 1=1
        """
        
        # Safely append drilldown predicate (parameterized value)
        query = f"{base_query} AND {field_name} = %(field_val)s"
        params = {
            "field_val": field_value,
            "department_id": filters.department_id if filters else None,
            "processor_id": filters.processor_id if filters else None,
            "start_date": filters.start_date if filters else None,
            "end_date": filters.end_date if filters else None
        }

        # Safely append active filters
        if filters:
            if filters.department_id:
                query += " AND DepartmentID = %(department_id)s"
            if filters.processor_id:
                query += " AND ProcessorID = %(processor_id)s"
            if filters.start_date:
                query += " AND DateCreated >= %(start_date)s"
            if filters.end_date:
                query += " AND DateCreated <= %(end_date)s"

        query += " ORDER BY DateCreated DESC"

        conn = self._get_connection()
        try:
            query = self._prepare_claims_query(query, conn, filters)
            self.validate_query(query)

            with conn.cursor() as cursor:
                self._execute_query(cursor, query, params)
                rows = cursor.fetchall()
                
                if cursor.description is None:
                    return {"columns": [], "rows": []}

                columns, cleaned_rows = self._rows_to_dicts(cursor, rows)

                return {
                    "columns": columns,
                    "rows": cleaned_rows
                }
        except Exception as e:
            logger.error(f"SQL execution error during claims drilldown: {e}")
            raise TargetDatabaseError(f"Drilldown SQL query failed: {e}")
        finally:
            conn.close()

# Single target database manager instance
target_db = SQLConnection()
