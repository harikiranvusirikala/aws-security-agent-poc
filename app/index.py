import json
import os
import sqlite3

DB_PATH = "/tmp/products.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS products "
        "(id INTEGER PRIMARY KEY, name TEXT, category TEXT, price REAL)"
    )
    cursor.execute("SELECT COUNT(*) FROM products")
    if cursor.fetchone()[0] == 0:
        cursor.executemany(
            "INSERT INTO products(name, category, price) VALUES (?, ?, ?)",
            [
                ("Laptop", "electronics", 1200.00),
                ("Keyboard", "electronics", 100.00),
                ("Headphones", "electronics", 150.00),
                ("Desk", "furniture", 350.00),
                ("Chair", "furniture", 250.00),
            ],
        )
    conn.commit()
    conn.close()


def lambda_handler(event, context):
    init_db()

    params = event.get("queryStringParameters") or {}
    category = params.get("category", "")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # INTENTIONALLY VULNERABLE - POC ONLY.
    # AWS Security Agent should detect SQL injection against this endpoint.
    query = (
        "SELECT name, category, price "
        "FROM products "
        "WHERE category = '"
        + category
        + "'"
    )

    try:
        rows = cursor.execute(query).fetchall()
        body = {"products": rows}
        status = 200
    except sqlite3.Error as exc:
        body = {"error": str(exc)}
        status = 500
    finally:
        conn.close()

    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }
