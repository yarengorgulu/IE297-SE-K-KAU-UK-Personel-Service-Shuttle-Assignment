"""Kalıcı personel servis atamaları için veri erişimi."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import Atama, Personel, Rota, Servis, ServisVardiyasi
from database.repositories.base import BaseRepository


@dataclass(frozen=True, slots=True)
class AssignedPersonnelSnapshot:
    assignment_id: int
    personnel_id: int
    personnel_number: str
    personnel_name: str
    route_name: str | None
    distance_meters: float | None
    pickup_latitude: float | None
    pickup_longitude: float | None
    assignment_date: datetime


class AssignmentRepository(BaseRepository[Atama]):
    model = Atama

    def active_for_personnel(self, personnel_id: int) -> Atama | None:
        return self.session.scalar(
            select(Atama).where(
                Atama.personel_id == personnel_id,
                Atama.aktif_mi.is_(True),
            )
        )

    def active_count_for_shift(self, shift_id: int) -> int:
        return int(
            self.session.scalar(
                select(func.count(Atama.id)).where(
                    Atama.servis_vardiya_id == shift_id,
                    Atama.aktif_mi.is_(True),
                )
            )
            or 0
        )

    def list_active_for_shift(
        self,
        shift_id: int,
    ) -> tuple[AssignedPersonnelSnapshot, ...]:
        rows = self.session.execute(
            select(Atama, Personel, Rota)
            .join(Personel, Personel.id == Atama.personel_id)
            .outerjoin(Rota, Rota.id == Atama.rota_id)
            .where(
                Atama.servis_vardiya_id == shift_id,
                Atama.aktif_mi.is_(True),
            )
            .order_by(Personel.sicil_no, Personel.id)
        ).all()
        return tuple(
            AssignedPersonnelSnapshot(
                assignment_id=assignment.id,
                personnel_id=person.id,
                personnel_number=person.sicil_no,
                personnel_name=person.ad_soyad
                or " ".join(part for part in (person.ad, person.soyad) if part)
                or person.sicil_no,
                route_name=None if route is None else route.rota_adi,
                distance_meters=assignment.guzergaha_uzaklik_metre,
                pickup_latitude=assignment.guzergahta_en_yakin_nokta_enlem,
                pickup_longitude=assignment.guzergahta_en_yakin_nokta_boylam,
                assignment_date=assignment.atama_tarihi,
            )
            for assignment, person, route in rows
        )

