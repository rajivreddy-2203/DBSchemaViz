"""Safe construction of Oracle and SQL Server source connections."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


@dataclass(frozen=True)
class SourceConnectionConfig:
    database_type: str
    host: str
    port: int | None
    database_name: str | None
    service_name: str | None
    instance_name: str | None
    default_schema: str | None
    username: str
    password: str


def build_source_engine(config: SourceConnectionConfig) -> Engine:
    """Build an Engine without logging a URL that could reveal the password."""
    user = quote_plus(config.username)
    password = quote_plus(config.password)
    host = config.host.strip()
    if not host:
        raise ValueError("A host is required.")

    if config.database_type == "oracle":
        target = host + (f":{config.port}" if config.port else "")
        if config.service_name:
            url = f"oracle+oracledb://{user}:{password}@{target}/?service_name={quote_plus(config.service_name)}"
        elif config.database_name:
            # SQLAlchemy's Oracle dialect treats the URL path as an Oracle SID.
            url = f"oracle+oracledb://{user}:{password}@{target}/{quote_plus(config.database_name)}"
        else:
            raise ValueError("Oracle requires a Service Name or SID.")
    elif config.database_type == "sqlserver":
        server = host
        if config.instance_name:
            server = f"{server}\\{config.instance_name}"
        if config.port:
            server = f"{server},{config.port}"
        if not config.database_name:
            raise ValueError("SQL Server requires a database name.")
        odbc = (
            "DRIVER={ODBC Driver 18 for SQL Server};"
            f"SERVER={server};DATABASE={config.database_name};"
            f"UID={config.username};PWD={config.password};"
            "Encrypt=yes;TrustServerCertificate=yes"
        )
        url = f"mssql+pyodbc:///?odbc_connect={quote_plus(odbc)}"
    else:
        raise ValueError("Database type must be oracle or sqlserver.")

    return create_engine(url, future=True, pool_pre_ping=True, hide_parameters=True)
