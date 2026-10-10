"""Read models for the graph, detail panel, searches, and Windchill analysis."""

from __future__ import annotations

import math

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased, joinedload, selectinload

from models.database import (
    Column,
    Connection,
    MetadataTable,
    Relationship,
    UnresolvedWindchillRelationship,
    WindchillClass,
    VisualizationPosition,
)
from windchill.discovery import CLASSNAME_A2A2, CLASSNAME_KEY_PREFIX, normalize_identifier


def qualified_name(table: MetadataTable) -> str:
    return f"{table.schema_name}.{table.name}"


def _table_query(connection_id: int):
    return (
        select(MetadataTable)
        .where(MetadataTable.connection_id == connection_id)
        .options(
            selectinload(MetadataTable.columns),
            selectinload(MetadataTable.primary_keys),
            selectinload(MetadataTable.positions),
        )
        .order_by(MetadataTable.schema_name, MetadataTable.name)
    )


def connection_stats(session: Session, connection_id: int) -> dict:
    connection = session.get(Connection, connection_id)
    if not connection:
        raise LookupError("Connection not found.")
    tables = session.scalar(select(func.count()).select_from(MetadataTable).where(MetadataTable.connection_id == connection_id)) or 0
    relationships = session.scalar(select(func.count()).select_from(Relationship).where(Relationship.connection_id == connection_id)) or 0
    unresolved = session.scalar(select(func.count()).select_from(UnresolvedWindchillRelationship).where(UnresolvedWindchillRelationship.connection_id == connection_id)) or 0
    return {
        "id": connection.id,
        "name": connection.name,
        "database_type": connection.database_type,
        "is_windchill": connection.is_windchill,
        "tables": tables,
        "relationships": relationships,
        "unresolved": unresolved,
    }


def graph_payload(
    session: Session,
    connection_id: int,
    limit: int = 500,
    offset: int = 0,
    query: str = "",
    table_id: int | None = None,
    include_external_relationships: bool = False,
) -> dict:
    stats = connection_stats(session, connection_id)
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    table_filter = None
    if query.strip():
        needle = f"%{normalize_identifier(query.strip())}%"
        table_filter = or_(
            func.lower(MetadataTable.name).like(needle),
            func.lower(MetadataTable.schema_name).like(needle),
        )
    table_statement = (
        select(
            MetadataTable.id,
            MetadataTable.schema_name,
            MetadataTable.name,
            select(func.count(Column.id))
            .where(Column.table_id == MetadataTable.id)
            .scalar_subquery()
            .label("column_count"),
            VisualizationPosition.x,
            VisualizationPosition.y,
        )
        .outerjoin(VisualizationPosition, VisualizationPosition.table_id == MetadataTable.id)
        .where(MetadataTable.connection_id == connection_id)
        .order_by(MetadataTable.schema_name, MetadataTable.name)
        .offset(offset)
        .limit(limit)
    )
    if table_filter is not None:
        table_statement = table_statement.where(table_filter)
    if table_id is not None:
        table_statement = table_statement.where(MetadataTable.id == table_id)
    table_rows = session.execute(table_statement)
    tables = list(table_rows)
    table_ids = [table.id for table in tables]
    source_column = aliased(Column)
    target_column = aliased(Column)
    relationship_rows = []
    if table_ids:
        relationship_statement = (
            select(
                Relationship.id,
                Relationship.source_table_id,
                Relationship.target_table_id,
                Relationship.relationship_type,
                Relationship.relationship_name,
                Relationship.constraint_name,
                Relationship.windchill_class,
                source_column.name.label("source_column_name"),
                target_column.name.label("target_column_name"),
            )
            .outerjoin(source_column, source_column.id == Relationship.source_column_id)
            .outerjoin(target_column, target_column.id == Relationship.target_column_id)
            .where(Relationship.connection_id == connection_id)
            .order_by(Relationship.relationship_type, Relationship.id)
        )
        if include_external_relationships:
            relationship_statement = relationship_statement.where(
                or_(
                    Relationship.source_table_id.in_(table_ids),
                    Relationship.target_table_id.in_(table_ids),
                )
            )
        else:
            relationship_statement = relationship_statement.where(
                Relationship.source_table_id.in_(table_ids),
                Relationship.target_table_id.in_(table_ids),
            )
        relationship_rows = session.execute(relationship_statement)
    nodes = []
    columns_per_row = max(1, math.ceil(math.sqrt(max(stats["tables"], 1))))
    for index, table in enumerate(tables):
        nodes.append({
            "data": {
                "id": str(table.id), "label": table.name,
                "qualified_name": f"{table.schema_name}.{table.name}",
                "card": table.name, "column_count": table.column_count,
            },
            "position": {"x": table.x, "y": table.y} if table.x is not None and table.y is not None else {
                "x": 180 + ((offset + index) % columns_per_row) * 260,
                "y": 120 + ((offset + index) // columns_per_row) * 190,
            },
        })
    edges = [
        {
            "data": {
                "id": f"r-{row.id}", "relationship_id": row.id,
                "source": str(row.source_table_id), "target": str(row.target_table_id),
                "type": row.relationship_type,
                "source_label": row.source_column_name or "",
                "target_label": row.target_column_name or "",
                "label": row.relationship_name or row.constraint_name or row.windchill_class or "",
            },
            "classes": row.relationship_type.lower(),
        }
        for row in relationship_rows
    ]
    return {
        "stats": stats,
        "page": {"limit": limit, "offset": offset, "returned": len(tables), "query": query},
        "elements": {"nodes": nodes, "edges": edges},
    }


def table_detail(session: Session, connection_id: int, table_id: int) -> dict:
    table = session.scalar(_table_query(connection_id).where(MetadataTable.id == table_id))
    if not table:
        raise LookupError("Table not found.")
    pk_by_column = {key.column_id: key for key in table.primary_keys}
    relationships = list(
        session.scalars(
            select(Relationship)
            .where(
                Relationship.connection_id == connection_id,
                or_(Relationship.source_table_id == table_id, Relationship.target_table_id == table_id),
            )
            .options(
                joinedload(Relationship.source_table), joinedload(Relationship.target_table),
                joinedload(Relationship.source_column), joinedload(Relationship.target_column),
            )
            .order_by(Relationship.relationship_type, Relationship.id)
        )
    )
    def summarize(relation: Relationship) -> dict:
        outgoing = relation.source_table_id == table_id
        other = relation.target_table if outgoing else relation.source_table
        return {
            "id": relation.id, "direction": "outgoing" if outgoing else "incoming",
            "type": relation.relationship_type,
            "other_table": qualified_name(other),
            "source_column": relation.source_column.name if relation.source_column else None,
            "target_column": relation.target_column.name if relation.target_column else None,
            "label": relation.constraint_name or relation.windchill_class or relation.relationship_name,
        }
    return {
        "id": table.id, "name": table.name, "schema": table.schema_name, "qualified_name": qualified_name(table),
        "columns": [
            {
                "id": column.id, "name": column.name, "data_type": column.data_type,
                "nullable": column.nullable, "position": column.ordinal_position,
                "primary_key": column.id in pk_by_column,
                "primary_key_order": pk_by_column[column.id].ordinal_position if column.id in pk_by_column else None,
            }
            for column in table.columns
        ],
        "relationships": [summarize(relation) for relation in relationships],
    }


def relationship_detail(session: Session, connection_id: int, relationship_id: int) -> dict:
    relation = session.scalar(
        select(Relationship)
        .where(Relationship.connection_id == connection_id, Relationship.id == relationship_id)
        .options(
            joinedload(Relationship.source_table), joinedload(Relationship.target_table),
            joinedload(Relationship.source_column), joinedload(Relationship.target_column),
        )
    )
    if not relation:
        raise LookupError("Relationship not found.")
    return {
        "id": relation.id, "type": relation.relationship_type,
        "source": {"table": qualified_name(relation.source_table), "column": relation.source_column.name if relation.source_column else None},
        "target": {"table": qualified_name(relation.target_table), "column": relation.target_column.name if relation.target_column else None},
        "constraint_name": relation.constraint_name, "windchill_class": relation.windchill_class,
        "classname_column": relation.classname_column, "ida_column": relation.ida_column,
        "discovery_method": relation.discovery_method, "confidence": relation.confidence,
        "metadata": relation.metadata_json or {},
    }


def search(session: Session, connection_id: int, query: str, limit: int = 30) -> list[dict]:
    needle = normalize_identifier(query.strip())
    if not needle:
        return []
    pattern = f"%{needle}%"
    results: list[dict] = []
    table_rows = session.execute(
        select(MetadataTable.id, MetadataTable.schema_name, MetadataTable.name)
        .where(
            MetadataTable.connection_id == connection_id,
            or_(func.lower(MetadataTable.name).like(pattern), func.lower(MetadataTable.schema_name).like(pattern)),
        )
        .order_by(MetadataTable.schema_name, MetadataTable.name)
        .limit(limit)
    )
    for row in table_rows:
        results.append({"kind": "table", "table_id": row.id, "label": f"{row.schema_name}.{row.name}"})

    column_rows = session.execute(
        select(
            Column.table_id,
            MetadataTable.schema_name.label("schema_name"),
            MetadataTable.name.label("table_name"),
            Column.name.label("column_name"),
        )
        .join(MetadataTable, MetadataTable.id == Column.table_id)
        .where(MetadataTable.connection_id == connection_id, func.lower(Column.name).like(pattern))
        .order_by(MetadataTable.schema_name, MetadataTable.name, Column.ordinal_position)
        .limit(limit)
    )
    for row in column_rows:
        results.append({"kind": "column", "table_id": row.table_id, "label": f"{row.schema_name}.{row.table_name}.{row.column_name}"})

    class_rows = session.execute(
        select(WindchillClass.class_name, WindchillClass.table_id, MetadataTable.schema_name, MetadataTable.name)
        .join(MetadataTable, MetadataTable.id == WindchillClass.table_id)
        .where(WindchillClass.connection_id == connection_id, WindchillClass.class_name_normalized.like(pattern))
        .order_by(WindchillClass.class_name)
        .limit(limit)
    )
    for row in class_rows:
        results.append({"kind": "class", "table_id": row.table_id, "label": f"{row.class_name} → {row.schema_name}.{row.name}"})
    seen = set()
    unique = []
    for row in results:
        signature = (row["kind"], row["label"])
        if signature not in seen:
            seen.add(signature)
            unique.append(row)
        if len(unique) >= limit:
            break
    return unique


def windchill_analysis(session: Session, connection_id: int) -> dict:
    total_tables = session.scalar(
        select(func.count()).select_from(MetadataTable).where(MetadataTable.connection_id == connection_id)
    ) or 0
    class_mapping_tables = session.scalar(
        select(func.count()).select_from(MetadataTable).where(
            MetadataTable.connection_id == connection_id,
            select(Column.id).where(
                Column.table_id == MetadataTable.id,
                func.lower(Column.name) == CLASSNAME_A2A2,
            ).exists(),
        )
    ) or 0
    classname_key_tables = session.scalar(
        select(func.count()).select_from(MetadataTable).where(
            MetadataTable.connection_id == connection_id,
            select(Column.id).where(
                Column.table_id == MetadataTable.id,
                func.lower(Column.name).like(f"{CLASSNAME_KEY_PREFIX}%"),
                func.lower(Column.name) != CLASSNAME_A2A2,
            ).exists(),
        )
    ) or 0
    class_rows = list(session.execute(
        select(
            WindchillClass.class_name,
            MetadataTable.schema_name,
            MetadataTable.name,
        )
        .join(MetadataTable, MetadataTable.id == WindchillClass.table_id)
        .where(WindchillClass.connection_id == connection_id)
        .order_by(WindchillClass.class_name)
    ))
    relation_count = session.scalar(
        select(func.count()).select_from(Relationship).where(
            Relationship.connection_id == connection_id,
            Relationship.relationship_type != "PK_FK",
        )
    ) or 0
    unresolved_count = session.scalar(
        select(func.count()).select_from(UnresolvedWindchillRelationship).where(
            UnresolvedWindchillRelationship.connection_id == connection_id
        )
    ) or 0
    return {
        "total_tables": total_tables, "tables_with_classname_a2a2": class_mapping_tables,
        "tables_with_classnamekey": classname_key_tables, "windchill_classes": len(class_rows),
        "windchill_relationships": relation_count, "unresolved": unresolved_count,
        "class_mappings": [
            {"class_name": row.class_name, "table": f"{row.schema_name}.{row.name}"}
            for row in class_rows
        ],
    }


def windchill_relationships(session: Session, connection_id: int) -> list[dict]:
    rows = session.scalars(
        select(Relationship)
        .where(Relationship.connection_id == connection_id, Relationship.relationship_type != "PK_FK")
        .options(
            joinedload(Relationship.source_table), joinedload(Relationship.target_table),
            joinedload(Relationship.source_column), joinedload(Relationship.target_column),
        )
        .order_by(Relationship.windchill_class, Relationship.id)
    )
    return [
        {
            "id": row.id, "source_table": qualified_name(row.source_table),
            "source_column": row.source_column.name if row.source_column else None,
            "target_table": qualified_name(row.target_table),
            "target_column": row.target_column.name if row.target_column else None,
            "windchill_class": row.windchill_class, "classname_column": row.classname_column,
            "discovery_method": row.discovery_method,
        }
        for row in rows
    ]


def unresolved_relationships(session: Session, connection_id: int) -> list[dict]:
    rows = session.scalars(
        select(UnresolvedWindchillRelationship)
        .where(UnresolvedWindchillRelationship.connection_id == connection_id)
        .options(joinedload(UnresolvedWindchillRelationship.source_table))
        .order_by(UnresolvedWindchillRelationship.id.desc())
    )
    return [
        {
            "id": row.id,
            "source_table": qualified_name(row.source_table) if row.source_table else None,
            "classname_column": row.classname_column, "ida_column": row.ida_column,
            "windchill_class": row.windchill_class, "reason": row.reason,
        }
        for row in rows
    ]
