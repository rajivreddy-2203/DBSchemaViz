"""Application configuration and local credential encryption."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv


if getattr(sys, "frozen", False):
    # PyInstaller one-file builds extract __file__ into a temporary directory.
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class Config:
    BASE_DIR = BASE_DIR
    INSTANCE_DIR = BASE_DIR / "instance"
    METADATA_DATABASE_URL = os.getenv(
        "METADATA_DATABASE_URL", f"sqlite:///{BASE_DIR / 'database_relationships.db'}"
    )
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "development-only-change-me")
    SQLALCHEMY_ECHO = os.getenv("SQLALCHEMY_ECHO", "0") == "1"
    JOB_WORKERS = int(os.getenv("JOB_WORKERS", "1"))


class CredentialCipher:
    """Encrypts passwords at rest; plaintext passwords are never stored in SQLite."""

    def __init__(self, instance_dir: Path, supplied_key: str | None = None):
        instance_dir.mkdir(parents=True, exist_ok=True)
        key_path = instance_dir / ".metadata_fernet.key"
        key = supplied_key.encode() if supplied_key else None
        if not key:
            if key_path.exists():
                key = key_path.read_bytes().strip()
            else:
                key = Fernet.generate_key()
                key_path.write_bytes(key)
                try:
                    os.chmod(key_path, 0o600)
                except OSError:
                    # Windows ACLs determine access; encryption still protects the DB itself.
                    pass
        self._fernet = Fernet(key)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
