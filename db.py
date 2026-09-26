"""
Database engine + session setup.

Replaces the flat-JSON storage (clients.json, report_links.json,
report_emails.json, site_ga4_map.json, site_gmb_map.json,
email_workflows.json, tracked_keywords.json, serper_keywords.json) with
Postgres, so the dashboard can handle 200-300 client portals without
read/write races or full-file rewrites on every change.

Connection string comes from DATABASE_URL, e.g.:
    postgresql+psycopg2://seo_user:secretpass@localhost:5432/seo_dashboard

Falls back to a local sqlite file (dashboard.db) if DATABASE_URL isn't
set, so existing dev setups (and this migration itself) keep working
without forcing a Postgres install on day one.
"""

import os
from contextlib import contextmanager
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///dashboard.db")

_engine_kwargs = {"pool_pre_ping": True}
if DATABASE_URL.startswith("postgresql"):
    # Tuned for ~200-300 client portals hitting the API concurrently.
    _engine_kwargs.update(pool_size=10, max_overflow=20, pool_recycle=1800)
elif DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


@contextmanager
def get_session():
    """Use as: with get_session() as session: ..."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db():
    """Creates any tables that don't exist yet. Safe to call on every
    startup — does nothing to tables that already exist."""
    import models  # noqa: F401 — registers models on Base before create_all
    Base.metadata.create_all(bind=engine)
