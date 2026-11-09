"""지도 DTO: Layer(명세 4절)."""

from pydantic import BaseModel

from app.repositories.map_layers import Layer


class LayerOut(BaseModel):
    id: str
    name: str
    entity_type: str
    available: bool
    source: str | None
    reference_date: str | None
    attribution: str | None

    @classmethod
    def of(cls, layer: Layer, available: bool) -> "LayerOut":
        return cls(
            id=layer.id,
            name=layer.name,
            entity_type=layer.entity_type,
            available=available,
            source=layer.source,
            reference_date=layer.reference_date,
            attribution=layer.attribution,
        )
