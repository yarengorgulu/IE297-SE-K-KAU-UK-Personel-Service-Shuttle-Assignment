"""Şoför veri erişim işlemleri."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from database.models import Servis, Sofor
from database.repositories.base import BaseRepository


class SoforRepository(BaseRepository[Sofor]):
    model = Sofor

    def __init__(self, session: Session) -> None:
        super().__init__(session)

    def list_ordered(self, *, include_inactive: bool = True) -> list[Sofor]:
        statement = select(Sofor).options(selectinload(Sofor.servisler))
        if not include_inactive:
            statement = statement.where(Sofor.aktif_mi.is_(True))
        statement = statement.order_by(Sofor.ad_soyad, Sofor.id)
        return list(self.session.scalars(statement).unique())

    def get_with_services(self, driver_id: int) -> Sofor | None:
        statement = (
            select(Sofor)
            .where(Sofor.id == driver_id)
            .options(selectinload(Sofor.servisler).selectinload(Servis.vardiyalar))
        )
        return self.session.scalar(statement)
