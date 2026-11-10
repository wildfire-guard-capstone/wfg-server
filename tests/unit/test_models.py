"""ORM models compile to the intended PostgreSQL DDL (no database needed)."""

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

import app.db.models  # noqa: F401
from app.db.base import Base

DIALECT = postgresql.dialect()


def ddl(table: str) -> str:
    return str(CreateTable(Base.metadata.tables[table]).compile(dialect=DIALECT))


def test_all_17_tables_registered():
    assert set(Base.metadata.tables) == {
        "users", "event_logs",
        "incidents", "fire_perimeters",
        "predictions", "prediction_slices",
        "proposals", "proposal_items",
        "situation_corrections", "qa_messages",
        "admin_areas", "villages", "facilities", "map_features",
        "resource_bases", "resources",
        "weather_hours",
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
    assert "hour BETWEEN 1 AND 8" in ddl("prediction_slices")


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


def test_proposal_item_stores_agent_output():
    # 11/9: state · screen sections · cited source text · reuse key; no chunk tables.
    item = ddl("proposal_items")
    assert "state VARCHAR(10) DEFAULT 'ok' NOT NULL" in item
    assert "state IN ('ok', 'partial', 'failed', 'reused')" in item
    assert "sections JSONB DEFAULT '[]'::jsonb NOT NULL" in item
    assert "evidence JSONB DEFAULT '[]'::jsonb NOT NULL" in item
    assert "input_key VARCHAR(16)" in item
    # A failed item has no paragraphs, so no categories.
    assert "categories item_category[] NOT NULL" in item
    assert "cardinality(categories)" not in item
    assert {"citations", "evidence_chunks"}.isdisjoint(Base.metadata.tables)


def test_weather_hours_one_row_per_issue():
    weather = ddl("weather_hours")
    assert "UNIQUE (location, source, issued_at, at)" in weather
    assert "source IN ('KMA_NCST', 'KMA_ULTRA', 'KMA_VILLAGE', 'ASOS')" in weather
    assert "wind_speed REAL" in weather
    assert "fetched_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in weather
