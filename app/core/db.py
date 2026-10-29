import logging
import os
import sqlite3

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

logger = logging.getLogger(__name__)

# Persistent database path
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////data/database.sqlite")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


if engine.dialect.name == "sqlite":
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        # The API, the watcher and the cron summary job all share this file.
        # WAL lets readers run alongside the watcher's writes, and avoids a
        # journal file create/fsync/delete on every commit.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA busy_timeout=30000")
        try:
            # Persistent in the file; a no-op once the database is already WAL
            cursor.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as e:
            logger.warning("Could not switch SQLite to WAL mode (will retry on next connection): %s", e)
        # Safe with WAL: a power loss can drop the last commits, never corrupt
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session
