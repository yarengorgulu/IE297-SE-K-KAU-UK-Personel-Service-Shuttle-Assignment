"""Servis-vardiya güzergâh noktaları veri erişim işlemleri."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from database.models import ServisGuzergahNoktasi, ServisVardiyasi
from database.repositories.base import BaseRepository


class RoutePointRepository(BaseRepository[ServisGuzergahNoktasi]):
    model = ServisGuzergahNoktasi

    def __init__(self, session: Session) -> None:
        super().__init__(session)

    def get_detail(self, point_id: int) -> ServisGuzergahNoktasi | None:
        statement = (
            select(ServisGuzergahNoktasi)
            .where(ServisGuzergahNoktasi.id == point_id)
            .options(
                selectinload(ServisGuzergahNoktasi.servis_vardiyasi).selectinload(
                    ServisVardiyasi.servis
                )
            )
        )
        return self.session.scalar(statement)

    def list_for_shift(
        self,
        shift_id: int,
        *,
        include_inactive: bool = True,
    ) -> list[ServisGuzergahNoktasi]:
        statement = (
            select(ServisGuzergahNoktasi)
            .where(ServisGuzergahNoktasi.servis_vardiya_id == shift_id)
            .options(
                selectinload(ServisGuzergahNoktasi.servis_vardiyasi).selectinload(
                    ServisVardiyasi.servis
                )
            )
            .order_by(ServisGuzergahNoktasi.sira_no)
        )
        if not include_inactive:
            statement = statement.where(ServisGuzergahNoktasi.aktif_mi.is_(True))
        return list(self.session.scalars(statement))

    def get_by_sequence(
        self,
        shift_id: int,
        sequence: int,
    ) -> ServisGuzergahNoktasi | None:
        statement = select(ServisGuzergahNoktasi).where(
            ServisGuzergahNoktasi.servis_vardiya_id == shift_id,
            ServisGuzergahNoktasi.sira_no == sequence,
        )
        return self.session.scalar(statement)

    def max_sequence(self, shift_id: int) -> int:
        statement = select(func.max(ServisGuzergahNoktasi.sira_no)).where(
            ServisGuzergahNoktasi.servis_vardiya_id == shift_id
        )
        return int(self.session.scalar(statement) or 0)

    def resequence(self, points: list[ServisGuzergahNoktasi]) -> None:
        if not points:
            return
        temporary_start = max(point.sira_no for point in points) + len(points) + 1000
        for index, point in enumerate(points):
            point.sira_no = temporary_start + index
        self.session.flush()
        for index, point in enumerate(points, start=1):
            point.sira_no = index
        self.session.flush()
