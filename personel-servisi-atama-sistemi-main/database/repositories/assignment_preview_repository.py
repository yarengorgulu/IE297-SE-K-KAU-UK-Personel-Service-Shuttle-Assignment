"""Aşama 12 atama önizlemesi için salt-okunur veri erişimi."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import (
    Atama,
    Personel,
    PersonelTuru,
    Rota,
    Servis,
    ServisVardiyasi,
    ServisVardiyaRota,
    VardiyaTuru,
    YakaTuru,
)


@dataclass(frozen=True, slots=True)
class PersonnelSnapshot:
    id: int
    personnel_number: str
    name: str
    personnel_type: PersonelTuru
    shift: VardiyaTuru
    collar: YakaTuru
    active: bool
    latitude: float | None
    longitude: float | None
    internship_start: date | None
    internship_end: date | None
    district: str | None
    neighborhood: str | None
    address: str
    street: str | None
    avenue: str | None
    city: str | None
    has_active_assignment: bool


@dataclass(frozen=True, slots=True)
class ShiftProfileSnapshot:
    shift_id: int
    service_code: str
    shift: VardiyaTuru
    collar: YakaTuru
    capacity: int
    occupancy: int


@dataclass(frozen=True, slots=True)
class RouteCandidateSnapshot:
    relation_id: int
    route_id: int
    route_name: str
    geometry_json: str
    shift_id: int
    service_code: str
    shift: VardiyaTuru
    collar: YakaTuru
    capacity: int
    occupancy: int


class AssignmentPreviewRepository:
    """Önizleme sırasında hiçbir ORM kaydını değiştirmeyen sorgu katmanı."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_personnel_by_number(self, personnel_number: str) -> PersonnelSnapshot | None:
        person = self.session.scalar(
            select(Personel).where(Personel.sicil_no == personnel_number.strip())
        )
        return None if person is None else self._personnel_snapshot(person)

    def get_personnel_by_ids(self, personnel_ids: tuple[int, ...]) -> tuple[PersonnelSnapshot, ...]:
        unique_ids = tuple(dict.fromkeys(int(item) for item in personnel_ids))
        if not unique_ids:
            return ()
        records = self.session.scalars(
            select(Personel).where(Personel.id.in_(unique_ids))
        ).all()
        by_id = {record.id: record for record in records}
        active_ids = self._active_assignment_personnel_ids(tuple(by_id))
        return tuple(
            self._personnel_snapshot(
                by_id[item], has_active_assignment=item in active_ids
            )
            for item in unique_ids
            if item in by_id
        )

    def list_personnel_options(self) -> tuple[PersonnelSnapshot, ...]:
        records = self.session.scalars(
            select(Personel).order_by(Personel.sicil_no, Personel.id)
        ).all()
        active_ids = self._active_assignment_personnel_ids(
            tuple(record.id for record in records)
        )
        return tuple(
            self._personnel_snapshot(
                record, has_active_assignment=record.id in active_ids
            )
            for record in records
        )

    def list_active_unassigned(self) -> tuple[PersonnelSnapshot, ...]:
        active_assignment = select(Atama.id).where(
            Atama.personel_id == Personel.id,
            Atama.aktif_mi.is_(True),
        ).exists()
        records = self.session.scalars(
            select(Personel)
            .where(Personel.aktif_mi.is_(True), ~active_assignment)
            .order_by(Personel.sicil_no, Personel.id)
        ).all()
        return tuple(
            self._personnel_snapshot(record, has_active_assignment=False)
            for record in records
        )

    def list_active_shift_profiles(self) -> tuple[ShiftProfileSnapshot, ...]:
        occupancy = (
            select(
                Atama.servis_vardiya_id.label("shift_id"),
                func.count(Atama.id).label("occupancy"),
            )
            .where(Atama.aktif_mi.is_(True))
            .group_by(Atama.servis_vardiya_id)
            .subquery()
        )
        rows = self.session.execute(
            select(
                ServisVardiyasi.id,
                Servis.servis_kodu,
                ServisVardiyasi.vardiya_turu,
                ServisVardiyasi.yaka_turu,
                ServisVardiyasi.kapasite,
                func.coalesce(occupancy.c.occupancy, 0),
            )
            .join(Servis, Servis.id == ServisVardiyasi.servis_id)
            .outerjoin(occupancy, occupancy.c.shift_id == ServisVardiyasi.id)
            .where(Servis.aktif_mi.is_(True), ServisVardiyasi.aktif_mi.is_(True))
            .order_by(Servis.servis_kodu, ServisVardiyasi.id)
        ).all()
        return tuple(
            ShiftProfileSnapshot(
                shift_id=int(row[0]),
                service_code=str(row[1]),
                shift=row[2],
                collar=row[3],
                capacity=int(row[4]),
                occupancy=int(row[5]),
            )
            for row in rows
        )

    def list_active_route_candidates(self) -> tuple[RouteCandidateSnapshot, ...]:
        profiles = {item.shift_id: item for item in self.list_active_shift_profiles()}
        if not profiles:
            return ()
        rows = self.session.execute(
            select(ServisVardiyaRota, Rota)
            .join(Rota, Rota.id == ServisVardiyaRota.rota_id)
            .where(
                ServisVardiyaRota.aktif_mi.is_(True),
                Rota.aktif_mi.is_(True),
                ServisVardiyaRota.servis_vardiya_id.in_(tuple(profiles)),
            )
            .order_by(ServisVardiyaRota.id, Rota.rota_adi, Rota.id)
        ).all()
        result: list[RouteCandidateSnapshot] = []
        for link, route in rows:
            profile = profiles.get(link.servis_vardiya_id)
            if profile is None:
                continue
            result.append(
                RouteCandidateSnapshot(
                    relation_id=link.id,
                    route_id=route.id,
                    route_name=route.rota_adi,
                    geometry_json=route.geojson,
                    shift_id=profile.shift_id,
                    service_code=profile.service_code,
                    shift=profile.shift,
                    collar=profile.collar,
                    capacity=profile.capacity,
                    occupancy=profile.occupancy,
                )
            )
        return tuple(result)

    def _personnel_snapshot(
        self,
        person: Personel,
        *,
        has_active_assignment: bool | None = None,
    ) -> PersonnelSnapshot:
        has_active = (
            bool(
                self.session.scalar(
                    select(func.count(Atama.id)).where(
                        Atama.personel_id == person.id,
                        Atama.aktif_mi.is_(True),
                    )
                )
            )
            if has_active_assignment is None
            else has_active_assignment
        )
        return PersonnelSnapshot(
            id=person.id,
            personnel_number=person.sicil_no,
            name=person.ad_soyad or " ".join(
                part for part in (person.ad, person.soyad) if part
            ) or person.sicil_no,
            personnel_type=person.personel_turu,
            shift=person.vardiya_turu,
            collar=person.yaka_turu,
            active=person.aktif_mi,
            latitude=person.enlem,
            longitude=person.boylam,
            internship_start=person.staj_baslangic_tarihi,
            internship_end=person.staj_bitis_tarihi,
            district=person.ilce,
            neighborhood=person.mahalle,
            address=person.acik_adres,
            street=person.sokak,
            avenue=person.cadde,
            city=person.il,
            has_active_assignment=has_active,
        )

    def _active_assignment_personnel_ids(
        self,
        personnel_ids: tuple[int, ...],
    ) -> set[int]:
        if not personnel_ids:
            return set()
        return {
            int(item)
            for item in self.session.scalars(
                select(Atama.personel_id)
                .where(
                    Atama.aktif_mi.is_(True),
                    Atama.personel_id.in_(personnel_ids),
                )
                .distinct()
            )
        }
