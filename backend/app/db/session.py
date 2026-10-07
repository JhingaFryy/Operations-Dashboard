from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings

settings = get_settings()

# Connects to the existing RDCMS database only. This engine must never be
# used with Base.metadata.create_all() or Alembic — the schema is owned and
# maintained outside this codebase (see app/db/models.py).
engine = create_engine(settings.rdcms_database_url, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
