import sqlite3
from pathlib import Path
from typing import Any
from .config import settings

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS Departments (
    DepartmentID INTEGER PRIMARY KEY,
    DepartmentName TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS Employees (
    EmployeeID INTEGER PRIMARY KEY,
    FirstName TEXT NOT NULL,
    LastName TEXT NOT NULL,
    Email TEXT,
    HireDate DATE NOT NULL,
    DepartmentID INTEGER,
    Salary REAL,
    State TEXT,
    FOREIGN KEY (DepartmentID) REFERENCES Departments(DepartmentID)
);
CREATE TABLE IF NOT EXISTS Customers (
    CustomerID INTEGER PRIMARY KEY,
    CustomerName TEXT NOT NULL,
    Email TEXT,
    State TEXT,
    SignupDate DATE
);
CREATE TABLE IF NOT EXISTS Orders (
    OrderID INTEGER PRIMARY KEY,
    CustomerID INTEGER NOT NULL,
    OrderDate DATE NOT NULL,
    Amount REAL NOT NULL,
    Status TEXT,
    FOREIGN KEY (CustomerID) REFERENCES Customers(CustomerID)
);
"""

SEED_SQL = """
INSERT OR IGNORE INTO Departments VALUES
(1,'Engineering'),(2,'Sales'),(3,'HR'),(4,'Finance');
INSERT OR IGNORE INTO Employees VALUES
(1,'Aarav','Sharma','aarav@example.com','2024-02-12',1,95000,'California'),
(2,'Meera','Patel','meera@example.com','2023-08-20',2,82000,'Texas'),
(3,'Rohan','Verma','rohan@example.com','2024-05-04',1,105000,'California'),
(4,'Isha','Singh','isha@example.com','2022-11-15',3,78000,'New York'),
(5,'Kabir','Gupta','kabir@example.com','2025-01-10',4,99000,'California');
INSERT OR IGNORE INTO Customers VALUES
(1,'Acme Corp','acme@example.com','California','2024-01-15'),
(2,'Globex','globex@example.com','Texas','2024-03-02'),
(3,'Initech','initech@example.com','California','2023-12-11'),
(4,'Umbrella','umbrella@example.com','New York','2025-02-01');
INSERT OR IGNORE INTO Orders VALUES
(1,1,'2025-01-05',1200,'Completed'),
(2,1,'2025-02-11',850,'Completed'),
(3,2,'2025-02-18',430,'Pending'),
(4,3,'2025-03-09',2100,'Completed'),
(5,4,'2025-03-21',640,'Cancelled');
"""

def get_connection() -> sqlite3.Connection:
    path = Path(settings.database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA_SQL)
        conn.executescript(SEED_SQL)

def get_schema() -> str:
    return SCHEMA_SQL

def get_schema_metadata() -> dict[str, set[str]]:
    with get_connection() as conn:
        tables: dict[str, set[str]] = {}
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            table = row[0]
            cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
            tables[table] = cols
        return tables

def get_foreign_keys() -> set[tuple[str, str, str, str]]:
    with get_connection() as conn:
        keys: set[tuple[str, str, str, str]] = set()
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            table = row[0]
            for fk in conn.execute(f'PRAGMA foreign_key_list("{table}")'):
                # id, seq, table, from, to, on_update, on_delete, match, ...
                keys.add((table, fk[3], fk[2], fk[4]))
        return keys

def execute_read_only(sql: str) -> list[dict[str, Any]]:
    path = Path(settings.database_path).resolve()
    uri_path = f"file:{path.as_posix()}?mode=ro"
    with sqlite3.connect(uri_path, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql)
        return [dict(row) for row in cur.fetchmany(settings.max_rows)]
