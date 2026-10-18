"""Repository sınıfları için ortak veri erişim işlemleri."""

from __future__ import annotations

from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Base


ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """Commit kararını servis katmanına bırakan temel repository."""

    model: type[ModelType]

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, entity: ModelType) -> ModelType:
        self.session.add(entity)
        self.session.flush()
        return entity

    def get_by_id(self, entity_id: int) -> ModelType | None:
        return self.session.get(self.model, entity_id)

    def list_all(self, include_inactive: bool = False) -> list[ModelType]:
        statement = select(self.model)
        if not include_inactive and hasattr(self.model, "aktif_mi"):
            statement = statement.where(self.model.aktif_mi.is_(True))
        return list(self.session.scalars(statement))

    def set_active(self, entity: ModelType, active: bool) -> ModelType:
        if not hasattr(entity, "aktif_mi"):
            raise TypeError(f"{type(entity).__name__} aktif/pasif durumunu desteklemiyor.")
        entity.aktif_mi = active
        self.session.flush()
        return entity
