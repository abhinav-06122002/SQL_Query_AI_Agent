import pytest
import sqlite3
from app.db import init_db, get_schema_metadata, execute_read_only

def test_database_schema_and_query():
    init_db()
    metadata = get_schema_metadata()
    assert {"Employees", "Departments", "Customers", "Orders"}.issubset(metadata)
    rows = execute_read_only("SELECT COUNT(*) AS n FROM Employees")
    assert rows[0]["n"] >= 5

def test_database_blocks_write_operations():
    init_db()
    with pytest.raises(sqlite3.OperationalError):
        execute_read_only("DELETE FROM Employees WHERE EmployeeID = 1")
