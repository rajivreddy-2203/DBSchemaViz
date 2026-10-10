"""Flask entry point for the database and Windchill relationship visualizer."""

from __future__ import annotations

import math
import os
from pathlib import Path

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from config import Config, CredentialCipher
from database.local import make_session_factory
from models.database import Connection, ExtractionRun, MetadataTable, VisualizationPosition
from services.extraction import ExtractionService
from services.relationship_service import (
    connection_stats,
    graph_payload,
    relationship_detail,
    search,
    table_detail,
    unresolved_relationships,
    windchill_analysis,
    windchill_relationships,
)


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_path=str(Config.INSTANCE_DIR), instance_relative_config=False)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    sessions = make_session_factory(app.config["METADATA_DATABASE_URL"], app.config["SQLALCHEMY_ECHO"])
    cipher = CredentialCipher(Path(app.instance_path), os.getenv("APP_ENCRYPTION_KEY"))
    app.extensions["metadata_sessions"] = sessions
    app.extensions["extraction_service"] = ExtractionService(sessions, cipher)

    def get_session():
        return sessions()

    def get_connection(connection_id: int, session):
        connection = session.get(Connection, connection_id)
        if not connection:
            abort(404)
        return connection

    @app.get("/")
    def index():
        return redirect(url_for("connections"))

    @app.get("/connections")
    def connections():
        with get_session() as session:
            rows = list(session.scalars(select(Connection).order_by(Connection.name)))
            data = []
            for connection in rows:
                latest = session.scalar(
                    select(ExtractionRun)
                    .where(ExtractionRun.connection_id == connection.id)
                    .order_by(ExtractionRun.id.desc())
                )
                data.append({
                    "connection": connection,
                    "run": latest,
                    "stats": connection_stats(session, connection.id),
                })
        return render_template("connections.html", connections=data)

    @app.post("/connections")
    def create_connection():
        form = request.form
        database_type = (form.get("database_type") or "").strip().lower()
        if database_type not in {"oracle", "sqlserver"}:
            flash("Choose Oracle or Microsoft SQL Server.", "error")
            return redirect(url_for("connections"))
        name = (form.get("name") or "").strip()
        host = (form.get("host") or "").strip()
        username = (form.get("username") or "").strip()
        password = form.get("password") or ""
        if not all((name, host, username, password)):
            flash("Connection name, host, username, and password are required.", "error")
            return redirect(url_for("connections"))
        try:
            port_value = (form.get("port") or "").strip()
            port = int(port_value) if port_value else (1521 if database_type == "oracle" else None)
            if port is not None and not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            flash("Port must be a number from 1 to 65535.", "error")
            return redirect(url_for("connections"))
        database_name = (form.get("database_name") or "").strip() or None
        service_name = (form.get("service_name") or "").strip() or None
        if database_type == "oracle" and not (service_name or database_name):
            flash("Oracle needs a Service Name or SID.", "error")
            return redirect(url_for("connections"))
        if database_type == "sqlserver" and not database_name:
            flash("SQL Server needs a database name.", "error")
            return redirect(url_for("connections"))

        with get_session() as session:
            if session.scalar(select(Connection).where(Connection.name == name)):
                flash("A saved connection already uses that name.", "error")
                return redirect(url_for("connections"))
            connection = Connection(
                name=name,
                database_type=database_type,
                host=host,
                port=port,
                database_name=database_name,
                service_name=service_name,
                instance_name=(form.get("instance_name") or "").strip() or None,
                default_schema=(form.get("default_schema") or "").strip() or None,
                username=username,
                encrypted_password=cipher.encrypt(password),
                is_windchill=form.get("is_windchill") == "on",
            )
            session.add(connection)
            session.commit()
            session.refresh(connection)
            connection_id = connection.id
        app.extensions["extraction_service"].start(connection_id)
        flash("Connection saved. Background metadata extraction has started.", "success")
        return redirect(url_for("dashboard", connection_id=connection_id))

    @app.get("/dashboard/<int:connection_id>")
    def dashboard(connection_id: int):
        with get_session() as session:
            connection = get_connection(connection_id, session)
            stats = connection_stats(session, connection_id)
        return render_template("dashboard.html", connection=connection, stats=stats)

    @app.get("/relationships/<int:connection_id>")
    def relationships_page(connection_id: int):
        with get_session() as session:
            connection = get_connection(connection_id, session)
        if not connection.is_windchill:
            flash("Windchill relationship analysis is available only for Windchill connections.", "error")
            return redirect(url_for("dashboard", connection_id=connection_id))
        return render_template("relationships.html", connection=connection)

    @app.get("/api/connections/<int:connection_id>/graph")
    def graph(connection_id: int):
        with get_session() as session:
            get_connection(connection_id, session)
            try:
                limit = int(request.args.get("limit", "500"))
                offset = int(request.args.get("offset", "0"))
                table_id = request.args.get("table_id")
                table_id = int(table_id) if table_id else None
            except ValueError:
                return jsonify({"error": "limit, offset, and table_id must be integers."}), 400
            return jsonify(graph_payload(
                session,
                connection_id,
                limit=limit,
                offset=offset,
                query=request.args.get("q", ""),
                table_id=table_id,
                include_external_relationships=request.args.get("include_external") == "1",
            ))

    @app.get("/api/connections/<int:connection_id>/stats")
    def stats(connection_id: int):
        with get_session() as session:
            get_connection(connection_id, session)
            return jsonify(connection_stats(session, connection_id))

    @app.post("/api/connections/<int:connection_id>/refresh")
    def refresh(connection_id: int):
        with get_session() as session:
            get_connection(connection_id, session)
        run = app.extensions["extraction_service"].start(connection_id)
        return jsonify({"run_id": run.id, "status": run.status, "phase": run.phase}), 202

    @app.get("/api/connections/<int:connection_id>/runs/latest")
    def latest_run(connection_id: int):
        with get_session() as session:
            get_connection(connection_id, session)
            run = session.scalar(
                select(ExtractionRun)
                .where(ExtractionRun.connection_id == connection_id)
                .order_by(ExtractionRun.id.desc())
            )
            if not run:
                return jsonify({"status": "idle", "phase": "No extraction has run", "completed_units": 0, "total_units": 1})
            return jsonify({
                "id": run.id, "status": run.status, "phase": run.phase,
                "completed_units": run.completed_units, "total_units": run.total_units,
                "message": run.message, "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            })

    @app.get("/api/connections/<int:connection_id>/tables/<int:table_id>")
    def table_details(connection_id: int, table_id: int):
        with get_session() as session:
            get_connection(connection_id, session)
            try:
                return jsonify(table_detail(session, connection_id, table_id))
            except LookupError:
                abort(404)

    @app.get("/api/connections/<int:connection_id>/relationships/<int:relationship_id>")
    def relationship_details(connection_id: int, relationship_id: int):
        with get_session() as session:
            get_connection(connection_id, session)
            try:
                return jsonify(relationship_detail(session, connection_id, relationship_id))
            except LookupError:
                abort(404)

    @app.get("/api/connections/<int:connection_id>/search")
    def search_endpoint(connection_id: int):
        with get_session() as session:
            get_connection(connection_id, session)
            return jsonify({"results": search(session, connection_id, request.args.get("q", ""))})

    @app.post("/api/connections/<int:connection_id>/tables/<int:table_id>/position")
    def save_position(connection_id: int, table_id: int):
        body = request.get_json(silent=True) or {}
        try:
            x, y = float(body["x"]), float(body["y"])
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "x and y must be finite numbers."}), 400
        with get_session() as session:
            table = session.scalar(
                select(MetadataTable).where(MetadataTable.id == table_id, MetadataTable.connection_id == connection_id)
            )
            if not table:
                abort(404)
            position = table.positions
            if position:
                position.x, position.y = x, y
            else:
                session.add(VisualizationPosition(table_id=table.id, x=x, y=y))
            session.commit()
        return "", 204

    @app.delete("/api/connections/<int:connection_id>/positions")
    def reset_positions(connection_id: int):
        with get_session() as session:
            get_connection(connection_id, session)
            table_ids = select(MetadataTable.id).where(MetadataTable.connection_id == connection_id)
            session.query(VisualizationPosition).filter(VisualizationPosition.table_id.in_(table_ids)).delete(
                synchronize_session=False
            )
            session.commit()
        return "", 204

    @app.get("/api/connections/<int:connection_id>/windchill/analysis")
    def windchill_analysis_endpoint(connection_id: int):
        with get_session() as session:
            connection = get_connection(connection_id, session)
            if not connection.is_windchill:
                return jsonify({"error": "This connection is not marked as Windchill."}), 400
            return jsonify(windchill_analysis(session, connection_id))

    @app.get("/api/connections/<int:connection_id>/windchill/relationships")
    def windchill_relationships_endpoint(connection_id: int):
        with get_session() as session:
            connection = get_connection(connection_id, session)
            if not connection.is_windchill:
                return jsonify({"error": "This connection is not marked as Windchill."}), 400
            return jsonify({"relationships": windchill_relationships(session, connection_id)})

    @app.get("/api/connections/<int:connection_id>/windchill/unresolved")
    def unresolved_endpoint(connection_id: int):
        with get_session() as session:
            connection = get_connection(connection_id, session)
            if not connection.is_windchill:
                return jsonify({"error": "This connection is not marked as Windchill."}), 400
            return jsonify({"relationships": unresolved_relationships(session, connection_id)})

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG", "0") == "1")
