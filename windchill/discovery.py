"""Windchill logical relationship discovery, deliberately separate from PK/FK discovery."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from database.metadata import CatalogDiscoverer
from models.database import (
    Column,
    MetadataTable,
    Relationship,
    UnresolvedWindchillRelationship,
    WindchillClass,
    WindchillClassColumn,
)


CLASSNAME_A2A2 = "classnamea2a2"
IDA_A2A2 = "ida2a2"
CLASSNAME_KEY_PREFIX = "classnamekey"
ROLE_REFERENCE_RULES = {
    "classnamekeyroleaobjectref": ("ida3a5", "WINDCHILL_ROLE_A"),
    "classnamekeyrolebobjectref": ("ida3b5", "WINDCHILL_ROLE_B"),
}
MASTER_REFERENCE_TYPE = "WINDCHILL_MASTER_REFERENCE"


def normalize_identifier(value: str) -> str:
    """Use Unicode case-folding for comparisons, retaining original identifiers for display."""
    return value.casefold()


def normalize_class_name(value: str) -> str:
    return value.strip().casefold()


def find_ida_match(classname_column: str, available_columns: list[str]) -> tuple[str | None, str | None]:
    """Resolve ``classnamekey<suffix>`` against actual ``ida[digits]<suffix>`` columns.

    A match is only accepted if there is exactly one candidate. This avoids silently
    choosing between similarly named historical/reference columns.
    """
    normalized = normalize_identifier(classname_column)
    if not normalized.startswith(CLASSNAME_KEY_PREFIX):
        return None, "Column is not a classnamekey column."
    suffix = normalized[len(CLASSNAME_KEY_PREFIX):]
    if not suffix:
        return None, "classnamekey column has no suffix."

    matches: list[str] = []
    for candidate in available_columns:
        candidate_normalized = normalize_identifier(candidate)
        if not candidate_normalized.startswith("ida"):
            continue
        remainder = candidate_normalized[3:]
        # Windchill commonly uses idA3<suffix>; tolerate another numeric idA prefix
        # only when the actual catalog has exactly one valid match.
        while remainder[:1].isdigit():
            remainder = remainder[1:]
        if remainder == suffix:
            matches.append(candidate)

    if len(matches) == 1:
        return matches[0], None
    if matches:
        return None, "Multiple ida columns match this classnamekey suffix; mapping is ambiguous."

    # Oracle limits unquoted identifiers to 30 characters, so a long
    # classnamekey suffix can be truncated differently from its ida column.
    truncated_matches = []
    for candidate in available_columns:
        candidate_normalized = normalize_identifier(candidate)
        if not candidate_normalized.startswith("ida"):
            continue
        remainder = candidate_normalized[3:]
        while remainder[:1].isdigit():
            remainder = remainder[1:]
        if remainder.startswith(suffix) or suffix.startswith(remainder):
            truncated_matches.append(candidate)
    if len(truncated_matches) == 1:
        return truncated_matches[0], None
    if len(truncated_matches) > 1:
        return None, "Multiple truncated ida columns match this classnamekey suffix; mapping is ambiguous."
    return None, "No matching ida column was found."


def find_explicit_id_match(expected_column: str, available_columns: list[str]) -> tuple[str | None, str | None]:
    normalized_expected = normalize_identifier(expected_column)
    matches = [column for column in available_columns if normalize_identifier(column) == normalized_expected]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, f"Expected {expected_column} column was not found."
    return None, f"Multiple {expected_column} columns were found."


def is_master_reference(column_name: str) -> bool:
    normalized = normalize_identifier(column_name)
    return normalized.startswith("ida3") and "masterreference" in normalized


def relationship_key(*parts: object) -> str:
    raw = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


ProgressCallback = Callable[[str, int, int, str], None]


class WindchillDiscovery:
    """Discover ``classnamekey -> ida -> classnameA2A2`` logical links.

    ``source`` is the vendor adapter. It owns identifier quoting and is the only
    component allowed to issue the generated DISTINCT source queries.
    """

    def __init__(self, session: Session, connection_id: int, source: CatalogDiscoverer):
        self.session = session
        self.connection_id = connection_id
        self.source = source

    def _tables(self) -> list[MetadataTable]:
        statement = (
            select(MetadataTable)
            .where(MetadataTable.connection_id == self.connection_id)
            .options(selectinload(MetadataTable.columns))
            .order_by(MetadataTable.schema_name, MetadataTable.name)
        )
        return list(self.session.scalars(statement))

    def _record_unresolved(
        self,
        table: MetadataTable,
        classname_column: str | None,
        ida_column: str | None,
        windchill_class: str | None,
        reason: str,
    ) -> None:
        self.session.add(
            UnresolvedWindchillRelationship(
                connection_id=self.connection_id,
                source_table_id=table.id,
                classname_column=classname_column,
                ida_column=ida_column,
                windchill_class=windchill_class,
                reason=reason,
            )
        )

    def _find_ida_for_classname_column(
        self,
        classname_column: Column,
        available_columns: list[str],
    ) -> tuple[str | None, str | None, str]:
        normalized_name = normalize_identifier(classname_column.name)
        role_rule = ROLE_REFERENCE_RULES.get(normalized_name)
        if role_rule:
            ida_name, reason = find_explicit_id_match(role_rule[0], available_columns)
            return ida_name, reason, role_rule[1]
        ida_name, reason = find_ida_match(classname_column.name, available_columns)
        return ida_name, reason, "WINDCHILL_CLASSNAME"

    def run(self, progress: ProgressCallback | None = None) -> dict[str, int]:
        tables = self._tables()
        total = max(len(tables), 1)

        # Pass 1: create classname -> table mappings exclusively from classnameA2A2.
        for index, table in enumerate(tables, start=1):
            class_column = next(
                (column for column in table.columns if normalize_identifier(column.name) == CLASSNAME_A2A2),
                None,
            )
            if class_column:
                try:
                    values = self.source.distinct_values(table.schema_name, table.name, class_column.name)
                except Exception as exc:
                    self._record_unresolved(
                        table, class_column.name, None, None,
                        f"Could not read distinct classnameA2A2 values ({type(exc).__name__}; permissions or query failure).",
                    )
                else:
                    for class_name in values:
                        self.session.add(
                            WindchillClass(
                                connection_id=self.connection_id,
                                table_id=table.id,
                                class_name=class_name,
                                class_name_normalized=normalize_class_name(class_name),
                            )
                        )
            if progress:
                progress("Discovering Windchill classes", index, total, f"Reading {table.schema_name}.{table.name}")

        self.session.flush()

        mappings: dict[str, list[WindchillClass]] = {}
        class_rows = self.session.scalars(
            select(WindchillClass).where(WindchillClass.connection_id == self.connection_id)
        )
        for row in class_rows:
            mappings.setdefault(row.class_name_normalized, []).append(row)

        # Windchill table identification is deliberately gated by the class
        # discriminator. This prevents unrelated Oracle tables from entering
        # logical relationship discovery.
        candidate_tables = [
            table for table in tables
            if any(normalize_identifier(column.name) == CLASSNAME_A2A2 for column in table.columns)
        ]

        # Pass 2: inspect every classnamekey column, including Role A/Role B,
        # and resolve each actual class value through the classnameA2A2 map.
        candidate_count = sum(
            1
            for table in candidate_tables
            for column in table.columns
            if normalize_identifier(column.name).startswith(CLASSNAME_KEY_PREFIX)
            and normalize_identifier(column.name) != CLASSNAME_A2A2
        )
        candidate_total = max(candidate_count, 1)
        created = 0

        relationship_total = max(len(candidate_tables), 1)
        table_index_by_id = {table.id: index for index, table in enumerate(candidate_tables, start=1)}
        for table in candidate_tables:
            columns_by_normalized = {normalize_identifier(column.name): column for column in table.columns}
            available_names = [column.name for column in table.columns]
            candidates = [
                column for column in table.columns
                if normalize_identifier(column.name).startswith(CLASSNAME_KEY_PREFIX)
                and normalize_identifier(column.name) != CLASSNAME_A2A2
            ]
            for classname_column in candidates:
                ida_name, reason, relationship_type = self._find_ida_for_classname_column(
                    classname_column, available_names
                )
                ida_column = columns_by_normalized.get(normalize_identifier(ida_name)) if ida_name else None
                status = "RESOLVED_CANDIDATE" if ida_column else "UNRESOLVED"
                self.session.add(
                    WindchillClassColumn(
                        table_id=table.id,
                        classname_column_id=classname_column.id,
                        ida_column_id=ida_column.id if ida_column else None,
                        status=status,
                        reason=reason,
                    )
                )
                if not ida_column:
                    self._record_unresolved(table, classname_column.name, None, None, reason or "No ida mapping.")
                    continue

                try:
                    class_values = self.source.distinct_values(table.schema_name, table.name, classname_column.name)
                except Exception as exc:
                    self._record_unresolved(
                        table, classname_column.name, ida_column.name, None,
                        f"Could not read distinct classnamekey values ({type(exc).__name__}; permissions or query failure).",
                    )
                    continue

                for class_value in class_values:
                    targets = mappings.get(normalize_class_name(class_value), [])
                    if not targets:
                        self._record_unresolved(
                            table, classname_column.name, ida_column.name, class_value,
                            "Windchill class was not found in any classnameA2A2 mapping.",
                        )
                        continue
                    if len(targets) > 1:
                        self._record_unresolved(
                            table, classname_column.name, ida_column.name, class_value,
                            "Windchill class maps to multiple tables; target is ambiguous.",
                        )
                        continue

                    target_table = self.session.get(MetadataTable, targets[0].table_id)
                    target_ida = next(
                        (column for column in target_table.columns if normalize_identifier(column.name) == IDA_A2A2),
                        None,
                    ) if target_table else None
                    if not target_ida:
                        self._record_unresolved(
                            table, classname_column.name, ida_column.name, class_value,
                            "Resolved target table does not have an ida2a2 column.",
                        )
                        continue

                    key = relationship_key(
                        self.connection_id, relationship_type, table.id, ida_column.id,
                        target_table.id, target_ida.id, classname_column.name, class_value,
                    )
                    self.session.add(
                        Relationship(
                            connection_id=self.connection_id,
                            source_table_id=table.id,
                            source_column_id=ida_column.id,
                            target_table_id=target_table.id,
                            target_column_id=target_ida.id,
                            relationship_type=relationship_type,
                            relationship_name=f"{table.name}.{ida_column.name} -> {target_table.name}.{target_ida.name}",
                            windchill_class=class_value,
                            classname_column=classname_column.name,
                            ida_column=ida_column.name,
                            discovery_method=f"{classname_column.name} -> distinct class -> classnameA2A2 -> ida2a2",
                            confidence=100,
                            metadata_json={"target_class_mapping_table_id": target_table.id},
                            relationship_key=key,
                        )
                    )
                    created += 1
            if progress:
                progress("Discovering Windchill links", table_index_by_id[table.id], relationship_total, f"Processed {table.schema_name}.{table.name}")

        created_count = [created]

        # Master references are structurally resolved to the matching *Master table.
        table_by_name = {
            normalize_identifier(table.name): table
            for table in tables
        }
        master_columns = [
            (table, column)
            for table in candidate_tables
            for column in table.columns
            if is_master_reference(column.name)
        ]
        for index, (table, source_column) in enumerate(master_columns, start=1):
            target_table = table_by_name.get(normalize_identifier(table.name) + "master")
            target_ida = next(
                (column for column in target_table.columns if normalize_identifier(column.name) == IDA_A2A2),
                None,
            ) if target_table else None
            if not target_table:
                self._record_unresolved(table, source_column.name, None, None, "No matching master table was found.")
            elif not target_ida:
                self._record_unresolved(table, source_column.name, None, None, "Matching master table does not have an ida2a2 column.")
            else:
                self.session.add(
                    Relationship(
                        connection_id=self.connection_id,
                        source_table_id=table.id,
                        source_column_id=source_column.id,
                        target_table_id=target_table.id,
                        target_column_id=target_ida.id,
                        relationship_type=MASTER_REFERENCE_TYPE,
                        relationship_name=f"{table.name}.{source_column.name} -> {target_table.name}.{target_ida.name}",
                        discovery_method="Windchill master-reference pattern",
                        confidence=100,
                        metadata_json={"master_table": target_table.name},
                        relationship_key=relationship_key(
                            self.connection_id, MASTER_REFERENCE_TYPE, table.id,
                            source_column.id, target_table.id, target_ida.id,
                        ),
                    )
                )
                created_count[0] += 1
            if progress:
                progress("Discovering Windchill master links", table_index_by_id[table.id], relationship_total, f"Processed {table.schema_name}.{table.name}")

        created = created_count[0]
        self.session.flush()
        return {
            "tables": len(tables),
            "classes": sum(len(values) for values in mappings.values()),
            "relationships": created,
            "candidate_columns": candidate_count,
        }
