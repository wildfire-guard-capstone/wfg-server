"""Column helpers shared by the models (DB 설계 「설계 원칙」)."""

import enum

from geoalchemy2 import Geometry
from sqlalchemy import DateTime, func, text
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import mapped_column

from app.db.base import Base

# Everything is stored in UTM-K; convert to 4326 only when sending to the map.
SRID = 5179


def geometry(geometry_type: str = "GEOMETRY", *, nullable: bool = False):
    return mapped_column(Geometry(geometry_type, srid=SRID), nullable=nullable)


_enums: dict[str, ENUM] = {}


def pg_enum(enum_cls: type[enum.Enum], name: str) -> ENUM:
    """One shared ENUM type per name, bound to the metadata so it is created once
    (also when used only inside an ARRAY)."""
    if name not in _enums:
        _enums[name] = ENUM(enum_cls, name=name, metadata=Base.metadata)
    return _enums[name]


def created_at():
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def jsonb_object():
    """NOT NULL jsonb defaulting to ``{}``."""
    return mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


def jsonb_array():
    """NOT NULL jsonb defaulting to ``[]``."""
    return mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
