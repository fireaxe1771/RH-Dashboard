import pymssql
import logging
import re
from typing import List, Dict, Any, Tuple
from config import settings
from models import DashboardFilters

logger = logging.getLogger(__name__)

class TargetDatabaseError(Exception):
    """Base exception for errors connecting to or querying target Azure SQL."""
    pass

class QueryValidationError(TargetDatabaseError):
    """Exception raised when a user-defined SQL query fails read-only security checks."""
    pass

class SQLConnection:
    """Manages raw connections and query execution against the target Azure SQL Database."""

    def _get_connection(self) -> pymssql.Connection:
        """Establishes a connection using parameters from configuration settings."""
        try:
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
        query_stripped = query_no_comments.strip()
        
        if not query_stripped.upper().startswith("SELECT"):
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
        # Validate query first
        self.validate_query(query)

        # Prepare parameters dictionary
        params = {
            "department_id": filters.department_id if filters else None,
            "processor_id": filters.processor_id if filters else None,
            "start_date": filters.start_date if filters else None,
            "end_date": filters.end_date if filters else None
        }

        conn = self._get_connection()
        try:
            with conn.cursor(as_dict=True) as cursor:
                # pymssql expects dict-based parameters in query string as %(name)s
                cursor.execute(query, params)
                rows = cursor.fetchall()
                
                # If query returned nothing (e.g. valid select with empty results)
                if cursor.description is None:
                    return {"columns": [], "rows": []}
                
                columns = [desc[0] for desc in cursor.description]
                
                # Format dates and decimal fields to standard types for JSON serialization
                cleaned_rows = []
                for row in rows:
                    cleaned_row = {}
                    for col in columns:
                        val = row[col]
                        # Handle datetime conversions
                        if hasattr(val, 'isoformat'):
                            cleaned_row[col] = val.isoformat()
                        # Convert Decimals to float
                        elif val.__class__.__name__ == 'Decimal':
                            cleaned_row[col] = float(val)
                        else:
                            cleaned_row[col] = val
                    cleaned_rows.append(cleaned_row)

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
            with conn.cursor(as_dict=True) as cursor:
                cursor.execute(schema_query)
                rows = cursor.fetchall()
                
                # Group columns by table names
                tables_map: Dict[str, List[Dict[str, str]]] = {}
                for row in rows:
                    tbl = row['TABLE_NAME']
                    col = row['COLUMN_NAME']
                    dtype = row['DATA_TYPE']
                    
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
            with conn.cursor(as_dict=True) as cursor:
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
                    "departments": [{"id": str(d['id']), "name": d['name']} for d in departments if d['id'] is not None],
                    "processors": [{"id": str(p['id']), "name": p['name']} for p in processors if p['id'] is not None],
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
            with conn.cursor(as_dict=True) as cursor:
                cursor.execute(query, params)
                rows = cursor.fetchall()
                
                if cursor.description is None:
                    return {"columns": [], "rows": []}
                
                columns = [desc[0] for desc in cursor.description]
                
                cleaned_rows = []
                for row in rows:
                    cleaned_row = {}
                    for col in columns:
                        val = row[col]
                        if hasattr(val, 'isoformat'):
                            cleaned_row[col] = val.isoformat()
                        elif val.__class__.__name__ == 'Decimal':
                            cleaned_row[col] = float(val)
                        else:
                            cleaned_row[col] = val
                    cleaned_rows.append(cleaned_row)

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
