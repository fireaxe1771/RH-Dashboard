import pymssql
import logging
import re
import struct
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

    def __init__(self):
        self._claims_date_column: str | None = None
        self._claims_column_map: Dict[str, str] | None = None

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

    def _prepare_claims_query(self, query: str, conn) -> str:
        """Normalizes legacy dashboard SQL before validation/execution."""
        if not re.search(r"\bClaims\b", query, re.IGNORECASE):
            return query

        claims_column_map = self._resolve_claims_column_map(conn)
        for legacy_name, actual_name in claims_column_map.items():
            if legacy_name == actual_name:
                continue
            query = re.sub(rf"\b{re.escape(legacy_name)}\b", actual_name, query)

        claims_date_column = self._resolve_claims_date_column(conn)
        return self._rewrite_claims_date_column(query, claims_date_column)

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

    def _get_connection(self):
        """Establishes a connection using parameters from configuration settings."""
        try:
            if settings.AZURE_SQL_AUTHENTICATION == "azure-ad":
                # Azure AD Service Principal authentication using token
                # Get Azure AD token using Service Principal credentials
                credential = ClientSecretCredential(
                    tenant_id=settings.AZURE_SQL_TENANT_ID,
                    client_id=settings.AZURE_SQL_USER,
                    client_secret=settings.AZURE_SQL_PASSWORD
                )
                
                # Get access token for Azure SQL Database
                token = credential.get_token("https://database.windows.net/.default")
                access_token = token.token
                
                # Connect using the access token through ODBC
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
            r"\bEXECUTE\b", r"\bGRANT\b", r"\bREVOKE\b"
        ]
        
        for pattern in destructive_patterns:
            if re.search(pattern, query_stripped, re.IGNORECASE):
                cleaned_pattern = pattern.strip(r' \b')
                raise QueryValidationError(f"Query Security Breach: Prohibited keyword detected: '{cleaned_pattern}'")

    def execute_read(self, query: str, filters: DashboardFilters = None) -> Dict[str, Any]:
        """Executes a SQL query with parameter bindings, returning columns and row values.
        
        Fails loudly by raising TargetDatabaseError if SQL syntax is incorrect or connections fail.
        """
        # Prepare parameters dictionary
        params = {
            "department_id": filters.department_id if filters else None,
            "processor_id": filters.processor_id if filters else None,
            "start_date": filters.start_date if filters else None,
            "end_date": filters.end_date if filters else None
        }

        conn = self._get_connection()
        try:
            query = self._prepare_claims_query(query, conn)
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
            query = self._prepare_claims_query(query, conn)
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
