"""ORM models for the 18 tables in wfg-docs `docs/설계/DB-설계.md`.

Importing this package registers every table on ``Base.metadata`` (Alembic needs that).
"""

from app.db.models.account import EventLog, User
from app.db.models.geo import AdminArea, Facility, MapFeature, Village
from app.db.models.handling import QaMessage, SituationCorrection
from app.db.models.incident import FirePerimeter, Incident
from app.db.models.prediction import Prediction, PredictionSlice
from app.db.models.proposal import Citation, EvidenceChunk, Proposal, ProposalItem
from app.db.models.resource import Resource, ResourceBase

__all__ = [
    "AdminArea",
    "Citation",
    "EventLog",
    "EvidenceChunk",
    "Facility",
    "FirePerimeter",
    "Incident",
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
]
