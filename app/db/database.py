from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import settings

# SQLite connect_args allows multi-threaded access for FastAPI & Streamlit
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """Dependency to retrieve database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """Create tables and apply additive SQLite schema migrations."""
    from app.db import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    # SQLAlchemy create_all() does not add columns to tables that already exist.
    # Keep this additive so existing workspaces, uploads, and audit records survive.
    if engine.dialect.name == "sqlite":
        inspector = inspect(engine)
        additive_columns = {
            "document_records": {
                "supplier": "VARCHAR(255)", "account_number": "VARCHAR(100)",
                "utility_type": "VARCHAR(50)", "bill_number": "VARCHAR(100)",
                "bill_date": "DATE", "period_start": "DATE", "period_end": "DATE",
                "is_excluded": "BOOLEAN DEFAULT 0",
            },
            "extracted_fields_audit": {
                "field_type": "VARCHAR(30)", "canonical_value": "FLOAT",
                "canonical_unit": "VARCHAR(30)", "reviewed": "BOOLEAN DEFAULT 0",
                "reviewed_by": "VARCHAR(100)", "reviewed_at": "DATETIME",
            },
        }
        for table, columns in additive_columns.items():
            if table not in inspector.get_table_names():
                continue
            present = {column["name"] for column in inspector.get_columns(table)}
            with engine.begin() as connection:
                for name, ddl in columns.items():
                    if name not in present:
                        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
        table_name = "cbam_default_values"
        if table_name in inspector.get_table_names():
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            with engine.begin() as connection:
                if "cn_code" not in existing_columns:
                    connection.execute(text(
                        "ALTER TABLE cbam_default_values ADD COLUMN cn_code VARCHAR(20)"
                    ))
                if "markup_pct" not in existing_columns:
                    connection.execute(text(
                        "ALTER TABLE cbam_default_values ADD COLUMN markup_pct FLOAT"
                    ))
                connection.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_cbam_default_values_cn_code "
                    "ON cbam_default_values (cn_code)"
                ))
