"""Oracle ALL_* catalog discovery, preserving Oracle's returned identifier casing."""

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


class OracleCatalogDiscoverer(CatalogDiscoverer):
    database_type = "oracle"

    def __init__(self, engine: Engine, schema: str | None = None):
        self.engine = engine
        self.schema = schema.strip().upper() if schema and schema.strip() else None

    @staticmethod
    def _text(value: object) -> str:
        return str(value)

    def snapshot(self, include_physical_relationships: bool = True) -> CatalogSnapshot:
        # Excluding Oracle-maintained schemas keeps a user with broad catalog access from
        # graphing the data dictionary itself. Any application schema remains included.
        schema_filter = "AND owner = :default_schema" if self.schema else ""
        tables_sql = text(f"""
            SELECT owner AS schema_name, table_name
            FROM all_tables
            WHERE owner NOT IN ('SYS', 'SYSTEM', 'XDB', 'MDSYS', 'CTXSYS', 'ORDSYS',
                                'WMSYS', 'DBSNMP', 'OUTLN', 'GSMADMIN_INTERNAL')
              {schema_filter}
            ORDER BY owner, table_name
        """)
        columns_sql = text(f"""
            SELECT c.owner AS schema_name, c.table_name, c.column_name,
                   c.data_type || CASE WHEN c.data_length IS NOT NULL THEN
                     '(' || c.data_length || ')' ELSE '' END AS data_type,
                   c.nullable, c.column_id
            FROM all_tab_columns c
            JOIN all_tables t ON t.owner = c.owner AND t.table_name = c.table_name
            WHERE c.owner NOT IN ('SYS', 'SYSTEM', 'XDB', 'MDSYS', 'CTXSYS', 'ORDSYS',
                                  'WMSYS', 'DBSNMP', 'OUTLN', 'GSMADMIN_INTERNAL')
              {schema_filter.replace('owner', 'c.owner')}
            ORDER BY c.owner, c.table_name, c.column_id
        """)
        primary_keys_sql = text(f"""
            SELECT c.owner AS schema_name, c.table_name, cc.column_name,
                   c.constraint_name, cc.position
            FROM all_constraints c
            JOIN all_cons_columns cc
              ON cc.owner = c.owner AND cc.constraint_name = c.constraint_name
            WHERE c.constraint_type = 'P'
              AND c.owner NOT IN ('SYS', 'SYSTEM', 'XDB', 'MDSYS', 'CTXSYS', 'ORDSYS',
                                  'WMSYS', 'DBSNMP', 'OUTLN', 'GSMADMIN_INTERNAL')
              {schema_filter.replace('owner', 'c.owner')}
            ORDER BY c.owner, c.table_name, c.constraint_name, cc.position
            """)
        foreign_keys_sql = text(f"""
            SELECT c.owner AS source_schema, c.table_name AS source_table,
                   cc.column_name AS source_column, r.owner AS target_schema,
                   r.table_name AS target_table, rcc.column_name AS target_column,
                   c.constraint_name, cc.position
            FROM all_constraints c
            JOIN all_cons_columns cc
              ON cc.owner = c.owner AND cc.constraint_name = c.constraint_name
            JOIN all_constraints r
              ON r.owner = c.r_owner AND r.constraint_name = c.r_constraint_name
            JOIN all_cons_columns rcc
              ON rcc.owner = r.owner AND rcc.constraint_name = r.constraint_name
             AND rcc.position = cc.position
            WHERE c.constraint_type = 'R'
              AND c.owner NOT IN ('SYS', 'SYSTEM', 'XDB', 'MDSYS', 'CTXSYS', 'ORDSYS',
                                  'WMSYS', 'DBSNMP', 'OUTLN', 'GSMADMIN_INTERNAL')
              {schema_filter.replace('owner', 'c.owner')}
            ORDER BY c.owner, c.table_name, c.constraint_name, cc.position
        """)
        parameters = {"default_schema": self.schema} if self.schema else {}
        with self.engine.connect() as conn:
            tables = [TableRecord(self._text(r.schema_name), self._text(r.table_name)) for r in conn.execute(tables_sql, parameters)]
            columns = [
                ColumnRecord(self._text(r.schema_name), self._text(r.table_name), self._text(r.column_name),
                             self._text(r.data_type), self._text(r.nullable).upper() == "Y", int(r.column_id))
                for r in conn.execute(columns_sql, parameters)
            ]
            primary_keys = []
            foreign_keys = []
            if include_physical_relationships:
                primary_keys = [
                    PrimaryKeyRecord(self._text(r.schema_name), self._text(r.table_name), self._text(r.column_name),
                                     self._text(r.constraint_name), int(r.position))
                    for r in conn.execute(primary_keys_sql, parameters)
                ]
                foreign_keys = [
                    ForeignKeyRecord(self._text(r.source_schema), self._text(r.source_table), self._text(r.source_column),
                                     self._text(r.target_schema), self._text(r.target_table), self._text(r.target_column),
                                     self._text(r.constraint_name), int(r.position))
                    for r in conn.execute(foreign_keys_sql, parameters)
                ]
        return CatalogSnapshot(tables, columns, primary_keys, foreign_keys)

    def quote_identifier(self, identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    def distinct_values(self, schema: str, table: str, column: str) -> list[str]:
        qualified = ".".join(map(self.quote_identifier, (schema, table)))
        sql = text(f"SELECT DISTINCT {self.quote_identifier(column)} AS class_value FROM {qualified} "
                   f"WHERE {self.quote_identifier(column)} IS NOT NULL")
        with self.engine.connect() as conn:
            return [str(row.class_value).strip() for row in conn.execute(sql) if row.class_value is not None and str(row.class_value).strip()]
