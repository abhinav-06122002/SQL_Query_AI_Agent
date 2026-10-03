import pytest
from app.guardrails import validate_read_only_sql, validate_schema_references, SQLValidationError

TABLES = {"Employees", "Departments", "Customers", "Orders"}
COLS = {
    "Employees": {"EmployeeID", "FirstName", "LastName", "Email", "HireDate", "DepartmentID", "Salary", "State"},
    "Departments": {"DepartmentID", "DepartmentName"},
    "Customers": {"CustomerID", "CustomerName", "Email", "State", "SignupDate"},
    "Orders": {"OrderID", "CustomerID", "OrderDate", "Amount", "Status"},
}

def test_select_is_allowed():
    assert "Employees" in validate_read_only_sql("SELECT * FROM Employees;")

def test_cte_and_union_are_allowed():
    assert validate_read_only_sql("WITH x AS (SELECT * FROM Employees) SELECT * FROM x")
    assert validate_read_only_sql("SELECT State FROM Employees UNION SELECT State FROM Customers")

@pytest.mark.parametrize("sql", [
    "DELETE FROM Employees",
    "UPDATE Employees SET Salary=0",
    "INSERT INTO Employees VALUES (9,'x','y',NULL,'2024-01-01',1,1,'x')",
    "DROP TABLE Employees",
    "ALTER TABLE Employees ADD COLUMN X TEXT",
    "TRUNCATE Employees",
    "PRAGMA table_info(Employees)",
    "SELECT * FROM Employees; DROP TABLE Employees",
])
def test_destructive_or_multi_statement_is_blocked(sql):
    with pytest.raises(SQLValidationError):
        validate_read_only_sql(sql)

def test_unknown_table_is_detected():
    errors = validate_schema_references("SELECT * FROM Payroll", TABLES, COLS)
    assert any("Unknown table" in e for e in errors)

def test_unknown_qualified_column_is_detected():
    errors = validate_schema_references("SELECT Employees.NotAColumn FROM Employees", TABLES, COLS)
    assert any("Unknown column" in e for e in errors)

def test_valid_join_has_no_schema_errors():
    sql = "SELECT e.FirstName, d.DepartmentName FROM Employees e JOIN Departments d ON e.DepartmentID = d.DepartmentID"
    assert validate_schema_references(sql, TABLES, COLS) == []
