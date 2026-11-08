"""ORM models compile to the intended PostgreSQL DDL (no database needed)."""

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

import app.db.models  # noqa: F401
from app.db.base import Base

DIALECT = postgresql.dialect()


def ddl(table: str) -> str:
    return str(CreateTable(Base.metadata.tables[table]).compile(dialect=DIALECT))


def test_all_18_tables_registered():
    assert set(Base.metadata.tables) == {
        "users", "event_logs",
        "incidents", "fire_perimeters",
        "predictions", "prediction_slices",
        "proposals", "proposal_items", "citations", "evidence_chunks",
        "situation_corrections", "qa_messages",
        "admin_areas", "villages", "facilities", "map_features",
        "resource_bases", "resources",
    }  # fmt: skip


def test_geometries_use_utm_k():
    assert "geometry(MULTIPOLYGON,5179)" in ddl("prediction_slices")
    assert "geometry(POINT,5179)" in ddl("incidents")
    assert "geometry(GEOMETRY,5179)" in ddl("facilities")


def test_shared_enum_is_one_type():
    stages = {
        Base.metadata.tables[t].c[c].type
        for t, c in [
            ("incidents", "current_stage"),
            ("proposals", "judged_stage"),
            ("proposals", "predicted_stage"),
        ]
    }
    assert len(stages) == 1


def test_constraints():
    assert "(status = 'DEPLOYED') = (deployed_incident_id IS NOT NULL)" in ddl("resources")
    assert "num_nonnulls(item_id, message_id) = 1" in ddl("citations")
    assert "hour BETWEEN 1 AND 8" in ddl("prediction_slices")
    assert "categories item_category[] NOT NULL" in ddl("proposal_items")


def test_login_id_unique_ignoring_case():
    (index,) = Base.metadata.tables["users"].indexes
    assert "lower(login_id)" in str(CreateIndex(index).compile(dialect=DIALECT))


def test_village_has_no_neighbor_ids():
    # Dropped on 11/4: shelter candidates are chosen by distance, not adjacency.
    assert "neighbor_ids" not in Base.metadata.tables["villages"].c


def test_no_alert_level():
    # 위기경보 dropped on 11/6: the rule engine never uses it (산림청 issues it).
    assert "alert_level" not in Base.metadata.tables["incidents"].c


def test_proposal_context():
    # Rule engine bundles for the version (rules.llm_input), added 11/8.
    assert "context JSONB DEFAULT '{}'::jsonb NOT NULL" in ddl("proposals")
