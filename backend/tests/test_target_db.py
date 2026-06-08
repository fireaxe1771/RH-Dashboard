import pytest
from unittest.mock import MagicMock, patch
from target_db import target_db, QueryValidationError, TargetDatabaseError
from models import DashboardFilters
from datetime import datetime

def test_query_validation_success():
    """Asserts that standard read-only select queries validate cleanly."""
    valid_queries = [
        "SELECT * FROM Claims",
        "  select RunNumber, Status FROM Claims WHERE 1=1",
        "/* Comment */ SELECT Count(*) FROM Claims GROUP BY Status",
        "SELECT Status FROM Claims -- Inline comment"
    ]
    for q in valid_queries:
        # Should not raise any exception
        target_db.validate_query(q)

def test_query_validation_destructive_keywords():
    """Asserts that queries containing dangerous modification statements are rejected."""
    invalid_queries = [
        "INSERT INTO Claims (Status) VALUES ('Draft')",
        "UPDATE Claims SET Status = 'Draft' WHERE ClaimID = 1",
        "DELETE FROM Claims",
        "DROP TABLE Claims",
        "ALTER TABLE Claims ADD Column1 int",
        "CREATE TABLE Test (ID int)",
        "TRUNCATE TABLE Claims",
        "SELECT * FROM Claims; DROP TABLE Logs",
        "SELECT * FROM Claims WHERE Status = 'Draft' -- DROP TABLE Logs"  # Destructive keyword inside comments can be tricky, but pattern is strict
    ]
    for q in invalid_queries:
        with pytest.raises(QueryValidationError) as excinfo:
            target_db.validate_query(q)
        assert "Query Security Breach" in str(excinfo.value)

def test_query_validation_cte_select():
    """Asserts that CTE-based read-only queries are allowed."""
    valid_query = ";WITH cte AS (SELECT * FROM Claims) SELECT * FROM cte"
    target_db.validate_query(valid_query)

def test_claims_date_column_rewrite():
    """Asserts that legacy DateCreated references can be normalized to the resolved Claims column."""
    query = "SELECT * FROM Claims WHERE DateCreated >= %(start_date)s ORDER BY DateCreated DESC"
    rewritten = target_db._rewrite_claims_date_column(query, "SysStartTime")

    assert "DateCreated" not in rewritten
    assert "SysStartTime" in rewritten

def test_claims_column_map_rewrite():
    """Asserts that legacy Claims dashboard column names can be mapped to live schema names."""
    target_db._claims_column_map = {
        "ClaimID": "RunNumber",
        "DateCreated": "SysStartTime",
        "submitted": "IsSubmitted",
        "original_run_id": "ParentRunID",
    }
    target_db._claims_date_column = "SysStartTime"

    query = (
        "SELECT COUNT(DISTINCT c.ClaimID) AS Count FROM Claims c "
        "WHERE c.submitted = 1 AND c.original_run_id IS NOT NULL "
        "AND c.DateCreated >= DATEFROMPARTS(YEAR(GETDATE()), 1, 1)"
    )
    rewritten = target_db._prepare_claims_query(query, MagicMock())

    assert "c.RunNumber" in rewritten
    assert "c.IsSubmitted" in rewritten
    assert "c.ParentRunID" in rewritten
    assert "SysStartTime" in rewritten
    assert "ClaimID" not in rewritten

@patch("target_db.SQLConnection._get_connection")
def test_execute_read_formatting(mock_connect):
    """Verifies decimal and date types returned by pyodbc/pymssql are formatted correctly for JSON."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    
    # Mock database cursor description and fetch outputs
    # Let Decimal represent a claim total, and datetime represent when it was logged
    from decimal import Decimal
    mock_cursor.description = [
        ('ClaimID', 3, None, None, None, None, None),
        ('Amount', 3, None, None, None, None, None),
        ('DateCreated', 3, None, None, None, None, None)
    ]
    mock_cursor.fetchall.return_value = [
        {
            'ClaimID': 1001,
            'Amount': Decimal('150.75'),
            'DateCreated': datetime(2026, 6, 4, 11, 0, 0)
        }
    ]
    
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_connect.return_value = mock_conn
    target_db._claims_date_column = "DateCreated"
    target_db._claims_column_map = {
        "ClaimID": "ClaimID",
        "DateCreated": "DateCreated",
    }

    # Execute query
    result = target_db.execute_read("SELECT ClaimID, Amount, DateCreated FROM Claims")
    
    assert result["columns"] == ["ClaimID", "Amount", "DateCreated"]
    assert len(result["rows"]) == 1
    
    row = result["rows"][0]
    assert row["ClaimID"] == 1001
    assert isinstance(row["Amount"], float)
    assert row["Amount"] == 150.75
    assert isinstance(row["DateCreated"], str)
    assert row["DateCreated"] == "2026-06-04T11:00:00"

@patch("target_db.SQLConnection._get_connection")
def test_execute_read_db_failure(mock_connect):
    """Verifies that actual target SQL failures fail loudly, raising TargetDatabaseError."""
    mock_connect.side_effect = Exception("SQL Server Login Timeout")
    
    with pytest.raises(TargetDatabaseError) as excinfo:
        target_db.execute_read("SELECT * FROM Claims")
    assert "Azure SQL Connection Failure" in str(excinfo.value)
