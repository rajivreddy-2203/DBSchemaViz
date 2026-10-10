"""Normalized SQLAlchemy schema for locally cached relationship metadata."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey as SAForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Connection(Base):
    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    database_type: Mapped[str] = mapped_column(String(30), nullable=False)  # oracle | sqlserver
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int | None] = mapped_column(Integer)
    database_name: Mapped[str | None] = mapped_column(String(255))
    service_name: Mapped[str | None] = mapped_column(String(255))
    instance_name: Mapped[str | None] = mapped_column(String(255))
    default_schema: Mapped[str | None] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_password: Mapped[str] = mapped_column(Text, nullable=False)
    is_windchill: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    tables: Mapped[list["MetadataTable"]] = relationship(
        back_populates="connection", cascade="all, delete-orphan"
    )
    runs: Mapped[list["ExtractionRun"]] = relationship(
        back_populates="connection", cascade="all, delete-orphan"
    )


class MetadataTable(Base):
    __tablename__ = "tables"
    __table_args__ = (
        UniqueConstraint("connection_id", "schema_name", "name", name="uq_table_identity"),
        Index("ix_tables_connection_name", "connection_id", "name"),
        Index("ix_tables_connection_schema_name", "connection_id", "schema_name", "name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(
        SAForeignKey("connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    schema_name: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    connection: Mapped[Connection] = relationship(back_populates="tables")
    columns: Mapped[list["Column"]] = relationship(
        back_populates="table", cascade="all, delete-orphan", foreign_keys="Column.table_id"
    )
    primary_keys: Mapped[list["PrimaryKey"]] = relationship(
        back_populates="table", cascade="all, delete-orphan"
    )
    positions: Mapped["VisualizationPosition | None"] = relationship(
        back_populates="table", cascade="all, delete-orphan", uselist=False
    )


class Column(Base):
    __tablename__ = "columns"
    __table_args__ = (
        UniqueConstraint("table_id", "name", name="uq_column_table_name"),
        Index("ix_columns_table_position", "table_id", "ordinal_position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    table_id: Mapped[int] = mapped_column(
        SAForeignKey("tables.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    data_type: Mapped[str] = mapped_column(String(255), nullable=False)
    nullable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ordinal_position: Mapped[int] = mapped_column(Integer, nullable=False)

    table: Mapped[MetadataTable] = relationship(
        back_populates="columns", foreign_keys=[table_id]
    )


class PrimaryKey(Base):
    __tablename__ = "primary_keys"
    __table_args__ = (UniqueConstraint("table_id", "column_id", name="uq_primary_key_column"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    table_id: Mapped[int] = mapped_column(
        SAForeignKey("tables.id", ondelete="CASCADE"), nullable=False, index=True
    )
    column_id: Mapped[int] = mapped_column(
        SAForeignKey("columns.id", ondelete="CASCADE"), nullable=False
    )
    constraint_name: Mapped[str] = mapped_column(String(255), nullable=False)
    ordinal_position: Mapped[int] = mapped_column(Integer, nullable=False)

    table: Mapped[MetadataTable] = relationship(back_populates="primary_keys")
    column: Mapped[Column] = relationship()


class ForeignKey(Base):
    __tablename__ = "foreign_keys"
    __table_args__ = (
        UniqueConstraint("connection_id", "constraint_name", "source_column_id", name="uq_fk_column"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(
        SAForeignKey("connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_table_id: Mapped[int] = mapped_column(SAForeignKey("tables.id", ondelete="CASCADE"), nullable=False)
    source_column_id: Mapped[int] = mapped_column(SAForeignKey("columns.id", ondelete="CASCADE"), nullable=False)
    target_table_id: Mapped[int] = mapped_column(SAForeignKey("tables.id", ondelete="CASCADE"), nullable=False)
    target_column_id: Mapped[int] = mapped_column(SAForeignKey("columns.id", ondelete="CASCADE"), nullable=False)
    constraint_name: Mapped[str] = mapped_column(String(255), nullable=False)
    ordinal_position: Mapped[int] = mapped_column(Integer, nullable=False)

    source_table: Mapped[MetadataTable] = relationship(foreign_keys=[source_table_id])
    source_column: Mapped[Column] = relationship(foreign_keys=[source_column_id])
    target_table: Mapped[MetadataTable] = relationship(foreign_keys=[target_table_id])
    target_column: Mapped[Column] = relationship(foreign_keys=[target_column_id])


class WindchillClass(Base):
    __tablename__ = "windchill_classes"
    __table_args__ = (
        UniqueConstraint("connection_id", "table_id", "class_name_normalized", name="uq_wc_class_table"),
        Index("ix_wc_class_lookup", "connection_id", "class_name_normalized"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(SAForeignKey("connections.id", ondelete="CASCADE"), nullable=False)
    table_id: Mapped[int] = mapped_column(SAForeignKey("tables.id", ondelete="CASCADE"), nullable=False)
    class_name: Mapped[str] = mapped_column(String(500), nullable=False)
    class_name_normalized: Mapped[str] = mapped_column(String(500), nullable=False)

    table: Mapped[MetadataTable] = relationship()


class WindchillClassColumn(Base):
    __tablename__ = "windchill_class_columns"
    __table_args__ = (UniqueConstraint("table_id", "classname_column_id", name="uq_wc_class_column"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    table_id: Mapped[int] = mapped_column(SAForeignKey("tables.id", ondelete="CASCADE"), nullable=False)
    classname_column_id: Mapped[int] = mapped_column(SAForeignKey("columns.id", ondelete="CASCADE"), nullable=False)
    ida_column_id: Mapped[int | None] = mapped_column(SAForeignKey("columns.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)

    table: Mapped[MetadataTable] = relationship(foreign_keys=[table_id])
    classname_column: Mapped[Column] = relationship(foreign_keys=[classname_column_id])
    ida_column: Mapped[Column | None] = relationship(foreign_keys=[ida_column_id])


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint("connection_id", "relationship_key", name="uq_relationship_key"),
        Index("ix_relationships_connection_type", "connection_id", "relationship_type"),
        Index("ix_relationships_connection_source_target", "connection_id", "source_table_id", "target_table_id"),
        Index("ix_relationships_connection_target_source", "connection_id", "target_table_id", "source_table_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(SAForeignKey("connections.id", ondelete="CASCADE"), nullable=False)
    source_table_id: Mapped[int] = mapped_column(SAForeignKey("tables.id", ondelete="CASCADE"), nullable=False)
    source_column_id: Mapped[int | None] = mapped_column(SAForeignKey("columns.id", ondelete="SET NULL"))
    target_table_id: Mapped[int] = mapped_column(SAForeignKey("tables.id", ondelete="CASCADE"), nullable=False)
    target_column_id: Mapped[int | None] = mapped_column(SAForeignKey("columns.id", ondelete="SET NULL"))
    relationship_type: Mapped[str] = mapped_column(String(50), nullable=False)
    relationship_name: Mapped[str | None] = mapped_column(String(255))
    constraint_name: Mapped[str | None] = mapped_column(String(255))
    windchill_class: Mapped[str | None] = mapped_column(String(500))
    classname_column: Mapped[str | None] = mapped_column(String(255))
    ida_column: Mapped[str | None] = mapped_column(String(255))
    discovery_method: Mapped[str | None] = mapped_column(String(255))
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    validation_status: Mapped[str] = mapped_column(String(30), nullable=False, default="NOT_VALIDATED")
    validation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    relationship_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    source_table: Mapped[MetadataTable] = relationship(foreign_keys=[source_table_id])
    source_column: Mapped[Column | None] = relationship(foreign_keys=[source_column_id])
    target_table: Mapped[MetadataTable] = relationship(foreign_keys=[target_table_id])
    target_column: Mapped[Column | None] = relationship(foreign_keys=[target_column_id])


class UnresolvedWindchillRelationship(Base):
    __tablename__ = "windchill_relationships"
    __table_args__ = (Index("ix_unresolved_connection", "connection_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(SAForeignKey("connections.id", ondelete="CASCADE"), nullable=False)
    source_table_id: Mapped[int | None] = mapped_column(SAForeignKey("tables.id", ondelete="SET NULL"))
    classname_column: Mapped[str | None] = mapped_column(String(255))
    ida_column: Mapped[str | None] = mapped_column(String(255))
    windchill_class: Mapped[str | None] = mapped_column(String(500))
    relationship_type: Mapped[str] = mapped_column(String(50), nullable=False, default="WINDCHILL_UNRESOLVED")
    candidate_tables: Mapped[list[str] | None] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source_table: Mapped[MetadataTable | None] = relationship()


class VisualizationPosition(Base):
    __tablename__ = "visualization_positions"
    __table_args__ = (UniqueConstraint("table_id", name="uq_visualization_table"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    table_id: Mapped[int] = mapped_column(SAForeignKey("tables.id", ondelete="CASCADE"), nullable=False)
    x: Mapped[float] = mapped_column(nullable=False)
    y: Mapped[float] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    table: Mapped[MetadataTable] = relationship(back_populates="positions")


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(SAForeignKey("connections.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    phase: Mapped[str] = mapped_column(String(100), nullable=False, default="Queued")
    completed_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_units: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    connection: Mapped[Connection] = relationship(back_populates="runs")
