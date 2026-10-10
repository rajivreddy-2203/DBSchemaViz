"""Local SQLite engine/session setup."""

from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from models.database import Base


def make_session_factory(url: str, echo: bool = False) -> sessionmaker[Session]:
    connect_args = (
        {"check_same_thread": False, "timeout": 60}
        if url.startswith("sqlite") else {}
    )
    engine = create_engine(url, echo=echo, future=True, connect_args=connect_args)

    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout=60000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    Base.metadata.create_all(engine)
    if url.startswith("sqlite"):
        _apply_sqlite_compatibility_migrations(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _apply_sqlite_compatibility_migrations(engine: Engine) -> None:
    """Additive migrations keep existing local metadata caches usable after upgrades."""
    additions = {
        "relationships": {
            "validation_status": "VARCHAR(30) NOT NULL DEFAULT 'NOT_VALIDATED'",
            "validation_count": "INTEGER NOT NULL DEFAULT 0",
            "validated_at": "DATETIME",
            "updated_at": "DATETIME",
        },
        "windchill_relationships": {
            "relationship_type": "VARCHAR(50) NOT NULL DEFAULT 'WINDCHILL_UNRESOLVED'",
            "candidate_tables": "JSON",
        },
    }
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_tables_connection_schema_name "
            "ON tables (connection_id, schema_name, name)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_relationships_connection_source_target "
            "ON relationships (connection_id, source_table_id, target_table_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_relationships_connection_target_source "
            "ON relationships (connection_id, target_table_id, source_table_id)"
        )
        for table, columns in additions.items():
            existing = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
            for column, definition in columns.items():
                if column not in existing:
                    connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
