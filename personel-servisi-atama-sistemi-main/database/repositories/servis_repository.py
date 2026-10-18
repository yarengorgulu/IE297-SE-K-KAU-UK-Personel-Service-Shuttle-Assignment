"""Servis ve servis vardiyası veri erişim işlemleri."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from database.models import (
    Atama,
    Servis,
    ServisVardiyasi,
    VardiyaTuru,
    YakaTuru,
)
from database.repositories.base import BaseRepository


class ServisRepository(BaseRepository[Servis]):
    model = Servis

    def __init__(self, session: Session) -> None:
        super().__init__(session)

    def get_by_code(self, service_code: str) -> Servis | None:
        statement = (
            select(Servis)
            .where(Servis.servis_kodu == service_code)
            .options(selectinload(Servis.vardiyalar))
        )
        return self.session.scalar(statement)

    def get_by_codes(self, service_codes: set[str]) -> list[Servis]:
        if not service_codes:
            return []
        statement = (
            select(Servis)
            .where(Servis.servis_kodu.in_(service_codes))
            .options(selectinload(Servis.vardiyalar))
        )
        return list(self.session.scalars(statement).unique())

    def get_detail(self, service_id: int) -> Servis | None:
        statement = (
            select(Servis)
            .where(Servis.id == service_id)
            .options(
                selectinload(Servis.sofor),
                selectinload(Servis.vardiyalar),
            )
        )
        return self.session.scalar(statement)

    def list_ordered(self, *, include_inactive: bool = True) -> list[Servis]:
        statement = select(Servis).options(
            selectinload(Servis.sofor),
            selectinload(Servis.vardiyalar),
        )
        if not include_inactive:
            statement = statement.where(Servis.aktif_mi.is_(True))
        statement = statement.order_by(Servis.servis_kodu)
        return list(self.session.scalars(statement).unique())

    def search_by_code(
        self,
        search_text: str,
        *,
        include_inactive: bool = True,
    ) -> list[Servis]:
        normalized_search = search_text.strip()
        if not normalized_search:
            return self.list_ordered(include_inactive=include_inactive)
        statement = (
            select(Servis)
            .where(Servis.servis_kodu.contains(normalized_search, autoescape=True))
            .options(
                selectinload(Servis.sofor),
                selectinload(Servis.vardiyalar),
            )
            .order_by(Servis.servis_kodu)
        )
        if not include_inactive:
            statement = statement.where(Servis.aktif_mi.is_(True))
        return list(self.session.scalars(statement).unique())


class ServisVardiyasiRepository(BaseRepository[ServisVardiyasi]):
    model = ServisVardiyasi

    def __init__(self, session: Session) -> None:
        super().__init__(session)

    def get_by_identity(
        self,
        service_id: int,
        shift: VardiyaTuru,
        collar: YakaTuru,
    ) -> ServisVardiyasi | None:
        statement = select(ServisVardiyasi).where(
            ServisVardiyasi.servis_id == service_id,
            ServisVardiyasi.vardiya_turu == shift,
            ServisVardiyasi.yaka_turu == collar,
        )
        return self.session.scalar(statement)

    def get_detail(self, shift_id: int) -> ServisVardiyasi | None:
        statement = (
            select(ServisVardiyasi)
            .where(ServisVardiyasi.id == shift_id)
            .options(selectinload(ServisVardiyasi.servis))
        )
        return self.session.scalar(statement)

    def list_ordered(
        self,
        *,
        service_id: int | None = None,
        include_inactive: bool = True,
    ) -> list[ServisVardiyasi]:
        statement = (
            select(ServisVardiyasi)
            .join(ServisVardiyasi.servis)
            .options(selectinload(ServisVardiyasi.servis))
        )
        if service_id is not None:
            statement = statement.where(ServisVardiyasi.servis_id == service_id)
        if not include_inactive:
            statement = statement.where(ServisVardiyasi.aktif_mi.is_(True))
        statement = statement.order_by(
            Servis.servis_kodu,
            ServisVardiyasi.vardiya_turu,
            ServisVardiyasi.yaka_turu,
        )
        return list(self.session.scalars(statement))

    def active_assignment_count(self, shift_id: int) -> int:
        statement = select(func.count()).select_from(Atama).where(
            Atama.servis_vardiya_id == shift_id,
            Atama.aktif_mi.is_(True),
        )
        return int(self.session.scalar(statement) or 0)
