"""Vendor-neutral catalog records used by the extraction service."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TableRecord:
    schema: str
    name: str


@dataclass(frozen=True)
class ColumnRecord:
    schema: str
    table: str
    name: str
    data_type: str
    nullable: bool
    ordinal_position: int


@dataclass(frozen=True)
class PrimaryKeyRecord:
    schema: str
    table: str
    column: str
    constraint_name: str
    ordinal_position: int


@dataclass(frozen=True)
class ForeignKeyRecord:
    source_schema: str
    source_table: str
    source_column: str
    target_schema: str
    target_table: str
    target_column: str
    constraint_name: str
    ordinal_position: int


@dataclass(frozen=True)
class CatalogSnapshot:
    tables: list[TableRecord]
    columns: list[ColumnRecord]
    primary_keys: list[PrimaryKeyRecord]
    foreign_keys: list[ForeignKeyRecord]


class CatalogDiscoverer:
    """The common interface implemented by Oracle and SQL Server adapters."""

    database_type: str

    def snapshot(self, include_physical_relationships: bool = True) -> CatalogSnapshot:  # pragma: no cover - abstract boundary
        raise NotImplementedError

    def distinct_values(self, schema: str, table: str, column: str) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    def quote_identifier(self, identifier: str) -> str:  # pragma: no cover
        raise NotImplementedError
