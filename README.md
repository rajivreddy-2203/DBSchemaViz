# Relationship Atlas: Database Schema Visualizer

Relationship Atlas is a web application for exploring database schemas from Oracle and Microsoft SQL Server. It builds interactive graph-based views of tables, relationships, and dependency patterns, with a specialized Windchill mode for discovering complex logical relationships in PTC Windchill databases.

The application caches schema metadata in a local SQLite database so the UI can render quickly without copying source business data.

## Features

- Interactive schema graph with draggable and searchable tables
- Support for Oracle and Microsoft SQL Server connections
- Standard mode for physical primary/foreign key relationships
- Windchill mode for logical relationship discovery, including classname-based and role-based links
- Background metadata extraction for faster workflow
- Local SQLite cache for fast analysis and offline browsing
- Detailed table and relationship inspection views
- Secure encrypted storage for saved database credentials
- Standalone packaging support for deployment

## Technology Stack

- Backend: Python 3, Flask, SQLAlchemy
- Frontend: HTML, CSS, JavaScript
- Graph Rendering: Cytoscape.js
- Source Databases: Oracle via `oracledb`, SQL Server via `pyodbc`
- Local Cache: SQLite
- Security: Fernet-based encryption for saved credentials
- Packaging: PyInstaller support via `relationship_atlas.spec`

## Installation and Usage

### Prerequisites

- Python 3.10+
- Pip
- Database client drivers depending on your environment:
  - SQL Server: Microsoft ODBC Driver for SQL Server
  - Oracle: Oracle Instant Client may be required

### Running from Source

1. Clone the repository:

```bash
git clone https://github.com/rajivreddy-2203/DBSchemaViz.git
cd DBSchemaViz
```

2. Create and activate a virtual environment:

```bash
python -m venv .venv
```

On Windows:

```powershell
.venv\Scripts\Activate.ps1
```

On macOS/Linux:

```bash
source .venv/bin/activate
```

3. Install project dependencies from the requirements file:

```bash
pip install -r requirements.txt
```

This ensures the required packages for Flask, SQLAlchemy, OracleDB, pyODBC, and the encryption library are installed in your active environment.

4. Configure environment variables (optional but recommended):

Create a `.env` file in the project root with the environment variables used by the app:

```env
# Server startup
PORT=5000
FLASK_DEBUG=0

# Flask app
FLASK_SECRET_KEY=your-generated-fernet-key

# Local metadata database
METADATA_DATABASE_URL=sqlite:///database_relationships.db

# Optional SQLAlchemy debug output
SQLALCHEMY_ECHO=0

# Background worker count
JOB_WORKERS=1

# Encryption key used to protect saved DB credentials
APP_ENCRYPTION_KEY=your-generated-fernet-key
```

Notes:

- `PORT` and `FLASK_DEBUG` are used when starting the app in `app.py`.
- `FLASK_SECRET_KEY` is used by Flask and should be a long random value in production.
- `METADATA_DATABASE_URL` defaults to a local SQLite database file if unset.
- `SQLALCHEMY_ECHO` turns SQL query logging on when set to `1`.
- `JOB_WORKERS` controls the number of background worker threads for extraction jobs.
- `APP_ENCRYPTION_KEY` is optional; if it is not provided, the app will generate a key automatically in the `instance/` directory.

5. Run the application:

```bash
python app.py
```

6. Open the app in your browser:

```text
http://127.0.0.1:5000
```

## How It Works

1. Connection Setup: a user adds connection details through the web interface.
2. Background Extraction: the app launches metadata extraction in the background.
3. Schema Discovery: vendor-specific code inspects Oracle or SQL Server catalog metadata and reads tables, columns, keys, and relationships.
4. Windchill Analysis: when enabled, the app looks for logical Windchill patterns such as classname mappings, `classnamekey` associations, Role A/Role B references, and master-replica relationships.
5. Local Caching: extracted schema data is stored in SQLite for fast graph queries and analysis.
6. Visualization: the frontend renders the graph and detail panels from that cached metadata.

## Project Structure

```text
.
├── app.py
├── config.py
├── database/
│   ├── __init__.py
│   ├── connection.py
│   ├── local.py
│   ├── metadata.py
│   ├── oracle.py
│   └── sqlserver.py
├── instance/
├── models/
│   ├── __init__.py
│   └── database.py
├── services/
│   ├── __init__.py
│   ├── extraction.py
│   └── relationship_service.py
├── static/
│   ├── css/
│   │   ├── app.css
│   │   └── dashboard.css
│   └── js/
│       ├── dashboard.js
│       └── relationships.js
├── templates/
│   ├── base.html
│   ├── connections.html
│   ├── dashboard.html
│   ├── index.html
│   ├── relationships.html
│   └── table_details.html
├── windchill/
│   ├── __init__.py
│   └── discovery.py
├── requirements.txt
├── relationship_atlas.spec
├── LICENSE
├── .gitignore
├── README.md
└── database_relationships.db
```

## Typical Workflow

1. Open the app and create a new connection.
2. Select the database type and input the required host, credentials, and database information.
3. Save the connection to trigger background extraction.
4. Visit the dashboard to view statistics and schema graph output.
5. Use the relationship view and search tools to inspect tables and links.
6. In Windchill mode, review logical relationships and unresolved mappings.

## PyInstaller Usage

This project includes a PyInstaller spec file named `relationship_atlas.spec`, which packages the Flask app into a single executable.

### Install PyInstaller

```bash
pip install pyinstaller
```

### Build the executable

From the project root:

```bash
pyinstaller relationship_atlas.spec
```

This reads the app entry point in `app.py` and bundles the required templates and static files from the `templates/` and `static/` directories.

### Build output

After the build completes, PyInstaller creates a `dist/` directory containing the generated executable. The default output will be:

```text
dist/relationship_atlas.exe
```

### Run the packaged app

```bash
./dist/relationship_atlas
```

Or on Windows:

```powershell
.\dist\relationship_atlas.exe
```

### Common build options

To keep the build output in a clean folder and rebuild from scratch:

```bash
pyinstaller --clean relationship_atlas.spec
```

### Notes

- The app uses `templates` and `static` as runtime assets, so the spec file includes them explicitly.
- If database drivers such as `oracledb` or `pyodbc` are not bundled correctly, rebuild with the spec file or add explicit hidden imports as needed.
- The packaged executable still expects access to the database and any required runtime environment or driver installation on the target machine.

## Notes

- Oracle connections may require a service name or SID.
- SQL Server connections require a database name.
- Saved passwords are encrypted before storage.
- This tool expects valid database access and permission to inspect metadata.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
