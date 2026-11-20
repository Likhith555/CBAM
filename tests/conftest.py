"""Shared fixtures: a temporary seeded SQLite database, an API client bound to
it, and the benchmark workspace."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_db
from app.db.seed import seed_session
from app.schemas.workspace import WorkspaceInputs
from tests.helpers import BENCHMARK_INPUTS


@pytest.fixture()
def db_engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    seed_session(session)
    session.close()
    yield engine
    engine.dispose()


@pytest.fixture()
def db(db_engine):
    session = sessionmaker(bind=db_engine, autoflush=False)()
    yield session
    session.close()


@pytest.fixture()
def api(db_engine):
    from app.main import app

    Session = sessionmaker(bind=db_engine, autoflush=False)

    def _get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _get_db
    yield TestClient(app)
    if previous is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = previous


@pytest.fixture()
def workspace(db):
    """A seeded workspace holding the fully measured benchmark inputs."""
    from app.modules.gaps.register import compute_gaps, get_or_create_workspace

    ws = get_or_create_workspace(db, "WS-BENCH")
    ws.inputs_json = WorkspaceInputs.model_validate(BENCHMARK_INPUTS).model_dump_json()
    compute_gaps(db, ws.id)
    db.commit()
    return ws


