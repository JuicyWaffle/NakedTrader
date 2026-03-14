"""
nakedtrader/db/session.py — Database connectie en sessie management.
"""

import logging
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from nakedtrader.db.models import Base

log = logging.getLogger(__name__)

class DatabaseManager:
    """Beheert de SQLAlchemy engine en sessies."""

    def __init__(self, db_url: str = "sqlite:///nakedtrader.db"):
        self.engine = create_engine(db_url, echo=False)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def init_db(self):
        """Maak de tabellen aan als ze nog niet bestaan."""
        try:
            Base.metadata.create_all(bind=self.engine)
            log.info("Database geïnitialiseerd")
        except Exception as e:
            log.error(f"Fout bij database initialisatie: {e}")

    def get_session(self) -> Session:
        """Maak een nieuwe database sessie."""
        return self.SessionLocal()

# Singleton-achtige toegang voor de orchestrator
_db_manager = None

def get_db_manager(db_url: str = None) -> DatabaseManager:
    global _db_manager
    if _db_manager is None:
        # Als geen URL opgegeven, zoek in de buurt van de config of project root
        if db_url is None:
             db_url = "sqlite:///nakedtrader.db"
        _db_manager = DatabaseManager(db_url)
    return _db_manager
