"""ORM models for the 17 tables in wfg-docs `docs/설계/DB-설계.md` + admin_villages(행정리, 11/19)
+ intake_records(기관별 접수 기록, 11/23).

Importing this package registers every table on ``Base.metadata`` (Alembic needs that).
"""

from app.db.models.account import EventLog, User
from app.db.models.geo import AdminArea, AdminVillage, Facility, MapFeature, Village
from app.db.models.handling import QaMessage, SituationCorrection
from app.db.models.incident import FirePerimeter, Incident, IntakeRecord
from app.db.models.prediction import Prediction, PredictionSlice
from app.db.models.proposal import Proposal, ProposalItem
from app.db.models.resource import Resource, ResourceBase
from app.db.models.weather import WeatherHour

__all__ = [
    "AdminArea",
    "AdminVillage",
    "EventLog",
    "Facility",
    "FirePerimeter",
    "Incident",
    "IntakeRecord",
    "MapFeature",
    "Prediction",
    "PredictionSlice",
    "Proposal",
    "ProposalItem",
    "QaMessage",
    "Resource",
    "ResourceBase",
    "SituationCorrection",
    "User",
    "Village",
    "WeatherHour",
]
