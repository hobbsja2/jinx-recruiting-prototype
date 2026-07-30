import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Load a local .env file if present so DATABASE_URL can live outside of git.
load_dotenv()

# Use DATABASE_URL from the environment (e.g. a Neon Postgres connection string)
# and fall back to a local SQLite file for local development.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./jinx_recruiting.db")

# Normalize the scheme some providers hand out ("postgres://") and pin the
# psycopg (v3) driver so it matches the dependency in requirements.txt.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# check_same_thread is a SQLite-only setting; Postgres needs a pooled connection
# that recycles idle connections (Neon closes them when the endpoint scales down).
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def sync_sqlite_columns() -> None:
    """Add columns introduced by model changes to existing SQLite tables.

    Keeps an already-seeded prototype database usable without dropping data.
    Only additive, nullable columns defined in our own models are applied.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in tables:
                continue
            present = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present or column.primary_key:
                    continue
                ddl = column.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl}'))
