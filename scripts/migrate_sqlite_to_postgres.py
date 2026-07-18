import os
import sqlite3
from pathlib import Path

import psycopg


TABLE_ORDER = [
    "users",
    "trips",
    "bookings",
    "payments",
    "notifications",
    "disputes",
    "sent_emails",
    "app_settings",
    "chat_messages",
    "admin_audit_logs",
]


def get_env(name, default=""):
    return os.environ.get(name, default).strip()


def reset_sequence(connection, table_name):
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_get_serial_sequence(%s, 'id')", (table_name,))
        sequence_name = cursor.fetchone()[0]
        if not sequence_name:
            return
        cursor.execute(
            """
            SELECT setval(
                %s,
                COALESCE((SELECT MAX(id) FROM {}), 1),
                true
            )
            """.format(table_name),
            (sequence_name,),
        )


def load_schema(connection, schema_path):
    sql = Path(schema_path).read_text(encoding="utf-8")
    with connection.cursor() as cursor:
        cursor.execute(sql)
    connection.commit()


def copy_table(sqlite_conn, pg_conn, table_name):
    sqlite_conn.row_factory = sqlite3.Row
    rows = sqlite_conn.execute(f"SELECT * FROM {table_name}").fetchall()
    if not rows:
        return 0

    columns = list(rows[0].keys())
    column_sql = ", ".join(columns)
    values_sql = ", ".join(["%s"] * len(columns))

    with pg_conn.cursor() as cursor:
        cursor.execute(f"TRUNCATE TABLE {table_name} CASCADE")
        cursor.executemany(
            f"INSERT INTO {table_name} ({column_sql}) VALUES ({values_sql})",
            [tuple(row[column] for column in columns) for row in rows],
        )
    pg_conn.commit()

    if "id" in columns:
        reset_sequence(pg_conn, table_name)
        pg_conn.commit()

    return len(rows)


def main():
    sqlite_path = get_env("MAGAYISA_DATABASE_PATH")
    postgres_dsn = get_env("MAGAYISA_POSTGRES_DSN")
    schema_path = get_env(
        "MAGAYISA_POSTGRES_SCHEMA_PATH",
        str(Path(__file__).with_name("postgres_schema.sql")),
    )

    if not sqlite_path:
        raise SystemExit("MAGAYISA_DATABASE_PATH is required")
    if not postgres_dsn:
        raise SystemExit("MAGAYISA_POSTGRES_DSN is required")

    sqlite_conn = sqlite3.connect(sqlite_path)
    pg_conn = psycopg.connect(postgres_dsn)

    try:
        load_schema(pg_conn, schema_path)
        total_rows = 0
        for table_name in TABLE_ORDER:
            copied = copy_table(sqlite_conn, pg_conn, table_name)
            total_rows += copied
            print(f"Copied {copied} row(s) from {table_name}")
        print(f"Done. Total copied rows: {total_rows}")
    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    main()
