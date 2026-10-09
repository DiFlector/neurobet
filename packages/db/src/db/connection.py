import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session


def get_database_url() -> str:
    """Build standard PostgreSQL connection URL from environment variables."""
    host = os.getenv("POSTGRES_HOST", "postgres")
    port = os.getenv("POSTGRES_INTERNAL_PORT", os.getenv("POSTGRES_PORT", "5432"))
    # If running from host against mapped port
    if host in ("localhost", "127.0.0.1"):
        port = os.getenv("POSTGRES_PORT", "5435")
    db = os.getenv("POSTGRES_DB", "neurobet")
    user = os.getenv("POSTGRES_USER", "neurobet")
    password = os.getenv("POSTGRES_PASSWORD", "neurobet_secure_pass")
    # Use explicit psycopg2 driver
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


engine = create_engine(get_database_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    """FastAPI dependency for obtaining a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
