"""SQL Server sys.* catalog discovery with ordered composite-key mappings."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from database.metadata import (
    CatalogDiscoverer,
    CatalogSnapshot,
    ColumnRecord,
    ForeignKeyRecord,
    PrimaryKeyRecord,
    TableRecord,
)


class SqlServerCatalogDiscoverer(CatalogDiscoverer):
    database_type = "sqlserver"

    def __init__(self, engine: Engine):
        self.engine = engine

    @staticmethod
    def _text(value: object) -> str:
        return str(value)

    def snapshot(self, include_physical_relationships: bool = True) -> CatalogSnapshot:
        tables_sql = text("""
            SELECT s.name AS schema_name, t.name AS table_name
            FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id
            WHERE t.is_ms_shipped = 0
            ORDER BY s.name, t.name
        """)
        columns_sql = text("""
            SELECT s.name AS schema_name, t.name AS table_name, c.name AS column_name,
                   ty.name + CASE WHEN ty.name IN ('varchar','nvarchar','char','nchar','binary','varbinary')
                       THEN '(' + CASE WHEN c.max_length = -1 THEN 'max'
                         WHEN ty.name IN ('nvarchar','nchar') THEN CONVERT(varchar(10), c.max_length / 2)
                         ELSE CONVERT(varchar(10), c.max_length) END + ')'
                       WHEN ty.name IN ('decimal','numeric') THEN '(' + CONVERT(varchar(10), c.precision) + ',' + CONVERT(varchar(10), c.scale) + ')'
                       ELSE '' END AS data_type,
                   c.is_nullable, c.column_id
            FROM sys.tables t
            JOIN sys.schemas s ON s.schema_id = t.schema_id
            JOIN sys.columns c ON c.object_id = t.object_id
            JOIN sys.types ty ON ty.user_type_id = c.user_type_id
            WHERE t.is_ms_shipped = 0
            ORDER BY s.name, t.name, c.column_id
        """)
        primary_keys_sql = text("""
            SELECT s.name AS schema_name, t.name AS table_name, c.name AS column_name,
                   kc.name AS constraint_name, ic.key_ordinal AS ordinal_position
            FROM sys.key_constraints kc
            JOIN sys.tables t ON t.object_id = kc.parent_object_id
            JOIN sys.schemas s ON s.schema_id = t.schema_id
            JOIN sys.index_columns ic ON ic.object_id = kc.parent_object_id AND ic.index_id = kc.unique_index_id
            JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
            WHERE kc.type = 'PK' AND ic.key_ordinal > 0 AND t.is_ms_shipped = 0
            ORDER BY s.name, t.name, kc.name, ic.key_ordinal
        """)
        foreign_keys_sql = text("""
            SELECT cs.name AS source_schema, ct.name AS source_table, cc.name AS source_column,
                   ts.name AS target_schema, tt.name AS target_table, tc.name AS target_column,
                   fk.name AS constraint_name, fkc.constraint_column_id AS ordinal_position
            FROM sys.foreign_keys fk
            JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
            JOIN sys.tables ct ON ct.object_id = fk.parent_object_id
            JOIN sys.schemas cs ON cs.schema_id = ct.schema_id
            JOIN sys.columns cc ON cc.object_id = ct.object_id AND cc.column_id = fkc.parent_column_id
            JOIN sys.tables tt ON tt.object_id = fk.referenced_object_id
            JOIN sys.schemas ts ON ts.schema_id = tt.schema_id
            JOIN sys.columns tc ON tc.object_id = tt.object_id AND tc.column_id = fkc.referenced_column_id
            WHERE ct.is_ms_shipped = 0 AND tt.is_ms_shipped = 0
            ORDER BY cs.name, ct.name, fk.name, fkc.constraint_column_id
        """)
        with self.engine.connect() as conn:
            tables = [TableRecord(self._text(r.schema_name), self._text(r.table_name)) for r in conn.execute(tables_sql)]
            columns = [
                ColumnRecord(self._text(r.schema_name), self._text(r.table_name), self._text(r.column_name),
                             self._text(r.data_type), bool(r.is_nullable), int(r.column_id))
                for r in conn.execute(columns_sql)
            ]
            primary_keys = []
            foreign_keys = []
            if include_physical_relationships:
                primary_keys = [
                    PrimaryKeyRecord(self._text(r.schema_name), self._text(r.table_name), self._text(r.column_name),
                                     self._text(r.constraint_name), int(r.ordinal_position))
                    for r in conn.execute(primary_keys_sql)
                ]
                foreign_keys = [
                    ForeignKeyRecord(self._text(r.source_schema), self._text(r.source_table), self._text(r.source_column),
                                     self._text(r.target_schema), self._text(r.target_table), self._text(r.target_column),
                                     self._text(r.constraint_name), int(r.ordinal_position))
                    for r in conn.execute(foreign_keys_sql)
                ]
        return CatalogSnapshot(tables, columns, primary_keys, foreign_keys)

    def quote_identifier(self, identifier: str) -> str:
        return "[" + identifier.replace("]", "]]" ) + "]"

    def distinct_values(self, schema: str, table: str, column: str) -> list[str]:
        qualified = ".".join(map(self.quote_identifier, (schema, table)))
        quoted_column = self.quote_identifier(column)
        sql = text(f"SELECT DISTINCT {quoted_column} AS class_value FROM {qualified} WHERE {quoted_column} IS NOT NULL")
        with self.engine.connect() as conn:
            return [str(row.class_value).strip() for row in conn.execute(sql) if row.class_value is not None and str(row.class_value).strip()]
