"""Asynchronous metadata extraction and idempotent cache replacement."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from config import CredentialCipher
from database.connection import SourceConnectionConfig, build_source_engine
from database.metadata import CatalogSnapshot
from database.oracle import OracleCatalogDiscoverer
from database.sqlserver import SqlServerCatalogDiscoverer
from models.database import (
    Column,
    Connection,
    ForeignKey,
    MetadataTable,
    PrimaryKey,
    Relationship,
    UnresolvedWindchillRelationship,
    VisualizationPosition,
    WindchillClass,
    WindchillClassColumn,
    ExtractionRun,
)
from windchill.discovery import WindchillDiscovery, relationship_key


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ExtractionService:
    def __init__(self, sessions: sessionmaker[Session], cipher: CredentialCipher):
        self.sessions = sessions
        self.cipher = cipher
        self._lock = threading.Lock()

    def start(self, connection_id: int) -> ExtractionRun:
        with self._lock, self.sessions() as session:
            active = session.scalar(
                select(ExtractionRun)
                .where(ExtractionRun.connection_id == connection_id, ExtractionRun.status.in_(("queued", "running")))
                .order_by(ExtractionRun.id.desc())
            )
            if active:
                return active
            if not session.get(Connection, connection_id):
                raise LookupError("Connection not found.")
            run = ExtractionRun(connection_id=connection_id, status="queued", phase="Queued")
            session.add(run)
            session.commit()
            session.refresh(run)
            thread = threading.Thread(target=self._run, args=(run.id,), daemon=True, name=f"extract-{run.id}")
            thread.start()
            return run

    def _set_progress(self, run_id: int, phase: str, complete: int, total: int, message: str | None = None) -> None:
        with self.sessions() as session:
            run = session.get(ExtractionRun, run_id)
            if run:
                run.phase = phase
                run.completed_units = complete
                run.total_units = max(total, 1)
                run.message = message
                session.commit()

    def _load_source(self, connection: Connection):
        config = SourceConnectionConfig(
            database_type=connection.database_type,
            host=connection.host,
            port=connection.port,
            database_name=connection.database_name,
            service_name=connection.service_name,
            instance_name=connection.instance_name,
            default_schema=connection.default_schema,
            username=connection.username,
            password=self.cipher.decrypt(connection.encrypted_password),
        )
        engine = build_source_engine(config)
        if connection.database_type == "oracle":
            return engine, OracleCatalogDiscoverer(engine, config.default_schema)
        return engine, SqlServerCatalogDiscoverer(engine)

    @staticmethod
    def _clear_metadata(session: Session, connection_id: int) -> None:
        """Delete cached graph data in dependency order before an atomic rebuild."""
        table_ids = select(MetadataTable.id).where(MetadataTable.connection_id == connection_id)
        session.execute(delete(UnresolvedWindchillRelationship).where(UnresolvedWindchillRelationship.connection_id == connection_id))
        session.execute(delete(Relationship).where(Relationship.connection_id == connection_id))
        session.execute(delete(ForeignKey).where(ForeignKey.connection_id == connection_id))
        session.execute(delete(WindchillClassColumn).where(WindchillClassColumn.table_id.in_(table_ids)))
        session.execute(delete(WindchillClass).where(WindchillClass.connection_id == connection_id))
        session.execute(delete(PrimaryKey).where(PrimaryKey.table_id.in_(table_ids)))
        session.execute(delete(VisualizationPosition).where(VisualizationPosition.table_id.in_(table_ids)))
        session.execute(delete(Column).where(Column.table_id.in_(table_ids)))
        session.execute(delete(MetadataTable).where(MetadataTable.connection_id == connection_id))

    @staticmethod
    def _persist_snapshot(
        session: Session,
        connection_id: int,
        snapshot: CatalogSnapshot,
        progress: Callable[[str, int, int, str], None] | None = None,
    ) -> dict[str, int]:
        ExtractionService._clear_metadata(session, connection_id)
        tables: dict[tuple[str, str], MetadataTable] = {}
        for row in snapshot.tables:
            table = MetadataTable(connection_id=connection_id, schema_name=row.schema, name=row.name)
            session.add(table)
            tables[(row.schema, row.name)] = table
        session.flush()
        if progress:
            progress("Persisting table metadata", 0, max(len(snapshot.tables), 1), "Tables loaded; persisting columns")

        columns: dict[tuple[str, str, str], Column] = {}
        processed_table_keys: set[tuple[str, str]] = set()
        for index, row in enumerate(snapshot.columns, start=1):
            table = tables.get((row.schema, row.table))
            if not table:
                continue
            processed_table_keys.add((row.schema, row.table))
            column = Column(
                table_id=table.id,
                name=row.name,
                data_type=row.data_type,
                nullable=row.nullable,
                ordinal_position=row.ordinal_position,
            )
            session.add(column)
            columns[(row.schema, row.table, row.name)] = column
            if index % 1000 == 0:
                session.flush()
                if progress:
                    progress("Persisting column metadata", len(processed_table_keys), max(len(snapshot.tables), 1), f"Processed {len(processed_table_keys):,} of {len(snapshot.tables):,} tables")
        session.flush()
        if progress:
            progress("Persisting column metadata", len(tables), max(len(snapshot.tables), 1), "Table and column metadata loaded")

        for row in snapshot.primary_keys:
            table = tables.get((row.schema, row.table))
            column = columns.get((row.schema, row.table, row.column))
            if table and column:
                session.add(
                    PrimaryKey(
                        table_id=table.id,
                        column_id=column.id,
                        constraint_name=row.constraint_name,
                        ordinal_position=row.ordinal_position,
                    )
                )

        physical_relationships = 0
        for row in snapshot.foreign_keys:
            source_table = tables.get((row.source_schema, row.source_table))
            source_column = columns.get((row.source_schema, row.source_table, row.source_column))
            target_table = tables.get((row.target_schema, row.target_table))
            target_column = columns.get((row.target_schema, row.target_table, row.target_column))
            if not all((source_table, source_column, target_table, target_column)):
                # A referenced table outside accessible catalog metadata cannot make a graph edge.
                continue
            session.add(
                ForeignKey(
                    connection_id=connection_id,
                    source_table_id=source_table.id,
                    source_column_id=source_column.id,
                    target_table_id=target_table.id,
                    target_column_id=target_column.id,
                    constraint_name=row.constraint_name,
                    ordinal_position=row.ordinal_position,
                )
            )
            session.add(
                Relationship(
                    connection_id=connection_id,
                    source_table_id=source_table.id,
                    source_column_id=source_column.id,
                    target_table_id=target_table.id,
                    target_column_id=target_column.id,
                    relationship_type="PK_FK",
                    relationship_name=row.constraint_name,
                    constraint_name=row.constraint_name,
                    discovery_method="Database foreign-key catalog",
                    confidence=100,
                    metadata_json={"composite_ordinal": row.ordinal_position},
                    relationship_key=relationship_key(
                        connection_id, "PK_FK", source_table.id, source_column.id,
                        target_table.id, target_column.id, row.constraint_name, row.ordinal_position,
                    ),
                )
            )
            physical_relationships += 1
        session.flush()
        return {
            "tables": len(tables),
            "columns": len(columns),
            "primary_keys": len(snapshot.primary_keys),
            "foreign_keys": physical_relationships,
        }

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        # Driver exceptions can echo a connection URL. Do not copy that into SQLite/UI.
        name = type(exc).__name__
        detail = str(exc).lower()
        if "cannot open database" in detail or "login failed" in detail:
            return f"{name}: login succeeded, but the account cannot open the requested database. Check the database name and grant this login access."
        if "timeout" in detail or "08001" in detail or "server was not found" in detail:
            return f"{name}: source database endpoint could not be reached. Check the host, SQL Server instance, and TCP port."
        return f"{name}: extraction failed. Check connectivity, credentials, and metadata permissions."

    def _run(self, run_id: int) -> None:
        engine = None
        try:
            with self.sessions() as session:
                run = session.get(ExtractionRun, run_id)
                if not run:
                    return
                run.status = "running"
                run.started_at = utcnow()
                run.phase = "Connecting"
                run.message = "Opening source database connection"
                connection = session.get(Connection, run.connection_id)
                session.commit()
                connection_id = connection.id
                is_windchill = connection.is_windchill

            self._set_progress(run_id, "Connecting", 0, 5, "Validating source database connection")
            with self.sessions() as session:
                connection = session.get(Connection, connection_id)
                engine, discoverer = self._load_source(connection)

            self._set_progress(run_id, "Discovering tables and columns", 1, 5, "Reading database catalog")
            snapshot = discoverer.snapshot(include_physical_relationships=not is_windchill)

            physical_phase = "Preparing Windchill metadata" if is_windchill else "Discovering PK/FK"
            physical_message = "Persisting table and column metadata" if is_windchill else "Persisting physical relationships"
            if is_windchill:
                self._set_progress(run_id, physical_phase, 0, max(len(snapshot.tables), 1), physical_message)
            else:
                self._set_progress(run_id, physical_phase, 2, 5, physical_message)
            with self.sessions.begin() as session:
                def snapshot_progress(phase: str, completed: int, total: int, message: str) -> None:
                    run = session.get(ExtractionRun, run_id)
                    if run:
                        run.phase = phase
                        run.completed_units = completed
                        run.total_units = max(total, 1)
                        run.message = message
                        session.flush()

                self._persist_snapshot(session, connection_id, snapshot, snapshot_progress)

            if is_windchill:
                with self.sessions.begin() as session:
                    def windchill_progress(phase: str, completed: int, total: int, message: str) -> None:
                        run = session.get(ExtractionRun, run_id)
                        if run:
                            run.phase = phase
                            run.completed_units = completed
                            run.total_units = max(total, 1)
                            run.message = message
                            session.flush()

                    WindchillDiscovery(session, connection_id, discoverer).run(windchill_progress)
                self._set_progress(run_id, "Building relationship graph", 5, 5, "Windchill logical analysis complete")
            else:
                self._set_progress(run_id, "Building relationship graph", 5, 5, "Physical relationship graph complete")

            with self.sessions() as session:
                run = session.get(ExtractionRun, run_id)
                run.status = "completed"
                run.phase = "Completed"
                run.completed_units = run.total_units
                run.message = "Extraction completed successfully"
                run.finished_at = utcnow()
                session.commit()
        except Exception as exc:
            with self.sessions() as session:
                run = session.get(ExtractionRun, run_id)
                if run:
                    run.status = "failed"
                    run.phase = "Failed"
                    run.message = self._safe_error(exc)
                    run.finished_at = utcnow()
                    session.commit()
        finally:
            if engine is not None:
                engine.dispose()
