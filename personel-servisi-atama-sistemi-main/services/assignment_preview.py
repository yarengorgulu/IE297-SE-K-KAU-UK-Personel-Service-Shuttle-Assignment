"""Aşama 12 personel-rota uygunluğu ve salt-okunur atama önizlemesi."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Mapping

from sqlalchemy.orm import Session, sessionmaker

from app.assignment_preview import (
    DEFAULT_PERSONNEL_ROUTE_MAX_DISTANCE_METERS,
    PERSONNEL_ROUTE_MAX_DISTANCE_SETTING_KEY,
)
from database.models import (
    Atama,
    AtamaDurumu,
    Personel,
    PersonelTuru,
    Rota,
    ServisVardiyaRota,
)
from database.repositories.assignment_repository import AssignmentRepository
from database.repositories.assignment_preview_repository import (
    AssignmentPreviewRepository,
    PersonnelSnapshot,
    RouteCandidateSnapshot,
)
from database.repositories.mapping_repository import ApplicationSettingRepository
from database.session import SessionFactory, session_scope
from services.assignment_geometry import (
    NearestRoutePoint,
    coordinates_from_geojson,
    nearest_point_on_linestring,
)


class AssignmentPreviewError(RuntimeError):
    """Kullanıcıya gösterilebilen önizleme hatası."""


@dataclass(frozen=True, slots=True)
class PersonnelOption:
    id: int
    personnel_number: str
    name: str
    active: bool
    has_active_assignment: bool
    address: str
    neighborhood: str | None
    street: str | None
    avenue: str | None
    district: str | None
    city: str | None
    latitude: float | None
    longitude: float | None

    @property
    def label(self) -> str:
        suffix = " / aktif ataması var" if self.has_active_assignment else ""
        return f"{self.personnel_number} - {self.name}{suffix}"

    @property
    def address_label(self) -> str:
        parts = (
            self.address,
            self.street,
            self.avenue,
            self.neighborhood,
            self.district,
            self.city,
        )
        unique: list[str] = []
        for value in parts:
            text = (value or "").strip()
            if text and text.casefold() not in {item.casefold() for item in unique}:
                unique.append(text)
        return " / ".join(unique) or "Adres bilgisi bulunmuyor."


@dataclass(frozen=True, slots=True)
class AssignmentAlternative:
    relation_id: int
    route_id: int
    shift_id: int
    service_code: str
    route_name: str
    distance_meters: float
    capacity: int
    occupancy: int
    remaining_capacity: int

    @property
    def label(self) -> str:
        return (
            f"{self.service_code} / {self.route_name} / "
            f"{self.distance_meters:.0f} m / kalan {self.remaining_capacity}"
        )


@dataclass(frozen=True, slots=True)
class AssignmentPreviewResult:
    personnel_id: int
    personnel_number: str
    personnel_name: str
    personnel_type: str
    shift: str
    collar: str
    personnel_latitude: float | None
    personnel_longitude: float | None
    service_code: str | None
    route_id: int | None
    relation_id: int | None
    route_name: str | None
    route_geometry: dict[str, object] | None
    capacity: int | None
    occupancy_before: int | None
    occupancy_after: int | None
    remaining_capacity: int | None
    distance_meters: float | None
    pickup_latitude: float | None
    pickup_longitude: float | None
    status: str
    reason: str
    alternatives: tuple[AssignmentAlternative, ...] = ()

    @property
    def assignable(self) -> bool:
        return self.status == "UYGUN"


@dataclass(frozen=True, slots=True)
class CapacityNeedSummary:
    shift: str
    collar: str
    personnel_count: int
    needed_capacity: int
    concentrated_areas: str
    suggested_route_or_area: str


@dataclass(frozen=True, slots=True)
class AssignmentPreview:
    method: str
    personnel_ids: tuple[int, ...]
    max_distance_meters: float
    results: tuple[AssignmentPreviewResult, ...]
    capacity_needs: tuple[CapacityNeedSummary, ...]

    @property
    def assignable_count(self) -> int:
        return sum(item.assignable for item in self.results)

    @property
    def unassignable_count(self) -> int:
        return len(self.results) - self.assignable_count


@dataclass(frozen=True, slots=True)
class AssignmentCommitItem:
    assignment_id: int
    personnel_id: int
    personnel_number: str
    service_code: str
    route_name: str


@dataclass(frozen=True, slots=True)
class AssignmentCommitResult:
    assignments: tuple[AssignmentCommitItem, ...]

    @property
    def assigned_count(self) -> int:
        return len(self.assignments)


@dataclass(frozen=True, slots=True)
class _EvaluatedCandidate:
    source: RouteCandidateSnapshot
    geometry: dict[str, object]
    nearest: NearestRoutePoint


class AssignmentPreviewService:
    """Kalıcı atama oluşturmadan uygunluk ve kapasite önizlemesi üretir."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionFactory,
        *,
        evaluation_date: date | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.evaluation_date = evaluation_date

    def get_max_distance_meters(self) -> float:
        with self.session_factory() as session:
            raw = ApplicationSettingRepository(session).get_value(
                PERSONNEL_ROUTE_MAX_DISTANCE_SETTING_KEY
            )
        if raw is None:
            return DEFAULT_PERSONNEL_ROUTE_MAX_DISTANCE_METERS
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return DEFAULT_PERSONNEL_ROUTE_MAX_DISTANCE_METERS
        return value if value > 0 else DEFAULT_PERSONNEL_ROUTE_MAX_DISTANCE_METERS

    def set_max_distance_meters(self, value: float) -> float:
        distance = self._validate_distance(value)
        with self.session_factory.begin() as session:
            ApplicationSettingRepository(session).set_value(
                PERSONNEL_ROUTE_MAX_DISTANCE_SETTING_KEY,
                str(distance),
            )
        return distance

    def list_personnel_options(self) -> tuple[PersonnelOption, ...]:
        with self.session_factory() as session:
            records = AssignmentPreviewRepository(session).list_personnel_options()
        return tuple(
            PersonnelOption(
                id=item.id,
                personnel_number=item.personnel_number,
                name=item.name,
                active=item.active,
                has_active_assignment=item.has_active_assignment,
                address=item.address,
                neighborhood=item.neighborhood,
                street=item.street,
                avenue=item.avenue,
                district=item.district,
                city=item.city,
                latitude=item.latitude,
                longitude=item.longitude,
            )
            for item in records
        )

    def commit_preview(
        self,
        preview: AssignmentPreview,
        *,
        personnel_ids: tuple[int, ...] | None = None,
    ) -> AssignmentCommitResult:
        """Açık kullanıcı onayından sonra uygun önerileri tek transaction ile atar."""
        selected_ids = None if personnel_ids is None else set(personnel_ids)
        selected = tuple(
            item
            for item in preview.results
            if item.assignable
            and (selected_ids is None or item.personnel_id in selected_ids)
        )
        if not selected:
            raise AssignmentPreviewError("Atanabilecek uygun bir önizleme sonucu seçilmedi.")

        committed: list[AssignmentCommitItem] = []
        try:
            with session_scope(self.session_factory) as session:
                assignments = AssignmentRepository(session)
                for result in selected:
                    person = session.get(Personel, result.personnel_id)
                    link = session.get(ServisVardiyaRota, result.relation_id)
                    if person is None or link is None:
                        raise AssignmentPreviewError(
                            f"{result.personnel_number}: personel veya rota bağlantısı artık bulunamıyor."
                        )
                    route = session.get(Rota, link.rota_id)
                    shift = link.servis_vardiyasi
                    physical_service = shift.servis
                    if assignments.active_for_personnel(person.id) is not None:
                        raise AssignmentPreviewError(
                            f"{person.sicil_no}: personelin zaten aktif bir ataması var."
                        )
                    if not (
                        person.aktif_mi
                        and link.aktif_mi
                        and route is not None
                        and route.aktif_mi
                        and shift.aktif_mi
                        and physical_service.aktif_mi
                    ):
                        raise AssignmentPreviewError(
                            f"{person.sicil_no}: personel, servis-vardiya veya rota artık aktif değil."
                        )
                    if (
                        person.vardiya_turu != shift.vardiya_turu
                        or person.yaka_turu != shift.yaka_turu
                    ):
                        raise AssignmentPreviewError(
                            f"{person.sicil_no}: vardiya veya yaka eşleşmesi değişmiş."
                        )
                    if person.personel_turu == PersonelTuru.STAJYER:
                        today = self.evaluation_date or date.today()
                        if (
                            person.staj_baslangic_tarihi is None
                            or person.staj_bitis_tarihi is None
                            or person.staj_baslangic_tarihi > today
                            or person.staj_bitis_tarihi < today
                        ):
                            raise AssignmentPreviewError(
                                f"{person.sicil_no}: stajyer tarih aralığı artık geçerli değil."
                            )
                    if person.enlem is None or person.boylam is None:
                        raise AssignmentPreviewError(
                            f"{person.sicil_no}: personel koordinatı artık bulunmuyor."
                        )
                    if assignments.active_count_for_shift(shift.id) >= shift.kapasite:
                        raise AssignmentPreviewError(
                            f"{physical_service.servis_kodu}: servis-vardiya kapasitesi doldu."
                        )
                    try:
                        geometry = json.loads(route.geojson)
                        coordinates = coordinates_from_geojson(geometry)
                        nearest = nearest_point_on_linestring(
                            person.enlem,
                            person.boylam,
                            coordinates,
                        )
                    except (TypeError, ValueError, json.JSONDecodeError) as error:
                        raise AssignmentPreviewError(
                            f"{route.rota_adi}: kayıtlı rota geometrisi artık geçerli değil."
                        ) from error
                    if nearest.distance_meters > preview.max_distance_meters:
                        raise AssignmentPreviewError(
                            f"{person.sicil_no}: rota uzaklığı yakınlık sınırını artık aşıyor."
                        )

                    assignment = assignments.add(
                        Atama(
                            personel_id=person.id,
                            servis_vardiya_id=shift.id,
                            rota_id=route.id,
                            guzergahta_en_yakin_nokta_enlem=nearest.latitude,
                            guzergahta_en_yakin_nokta_boylam=nearest.longitude,
                            guzergaha_uzaklik_metre=nearest.distance_meters,
                            atama_durumu=AtamaDurumu.ATANDI,
                            atama_aciklamasi=(
                                "Personel Atama ekranındaki kullanıcı onayıyla "
                                f"{route.rota_adi} rotasına atandı."
                            ),
                            aktif_mi=True,
                        )
                    )
                    committed.append(
                        AssignmentCommitItem(
                            assignment_id=assignment.id,
                            personnel_id=person.id,
                            personnel_number=person.sicil_no,
                            service_code=physical_service.servis_kodu,
                            route_name=route.rota_adi,
                        )
                    )
        except AssignmentPreviewError:
            raise
        except Exception as error:
            raise AssignmentPreviewError(
                "Atama işlemi tamamlanamadı; yapılan işlemlerin tamamı geri alındı."
            ) from error
        return AssignmentCommitResult(tuple(committed))

    def preview_single(
        self,
        personnel_number: str,
        *,
        max_distance_meters: float | None = None,
    ) -> AssignmentPreview:
        number = personnel_number.strip()
        if not number:
            raise AssignmentPreviewError("Sicil numarası girilmelidir.")
        with self.session_factory() as session:
            repository = AssignmentPreviewRepository(session)
            person = repository.get_personnel_by_number(number)
            if person is None:
                raise AssignmentPreviewError("Personel kaydı bulunamadı.")
            return self._build_preview(
                repository,
                (person,),
                method="TEK_PERSONEL",
                max_distance_meters=max_distance_meters,
                manual_choices={},
            )

    def preview_selected(
        self,
        personnel_ids: tuple[int, ...],
        *,
        max_distance_meters: float | None = None,
        manual_choices: Mapping[int, int] | None = None,
    ) -> AssignmentPreview:
        ids = tuple(dict.fromkeys(int(item) for item in personnel_ids))
        if not ids:
            raise AssignmentPreviewError("En az bir personel seçilmelidir.")
        with self.session_factory() as session:
            repository = AssignmentPreviewRepository(session)
            personnel = repository.get_personnel_by_ids(ids)
            if not personnel:
                raise AssignmentPreviewError("Seçilen personel kayıtları bulunamadı.")
            return self._build_preview(
                repository,
                personnel,
                method="SECILI_PERSONEL",
                max_distance_meters=max_distance_meters,
                manual_choices=manual_choices or {},
            )

    def preview_all_unassigned(
        self,
        *,
        max_distance_meters: float | None = None,
        manual_choices: Mapping[int, int] | None = None,
    ) -> AssignmentPreview:
        with self.session_factory() as session:
            repository = AssignmentPreviewRepository(session)
            personnel = repository.list_active_unassigned()
            return self._build_preview(
                repository,
                personnel,
                method="TUM_UYGUN_ATANMAMIS",
                max_distance_meters=max_distance_meters,
                manual_choices=manual_choices or {},
            )

    def _build_preview(
        self,
        repository: AssignmentPreviewRepository,
        personnel: tuple[PersonnelSnapshot, ...],
        *,
        method: str,
        max_distance_meters: float | None,
        manual_choices: Mapping[int, int],
    ) -> AssignmentPreview:
        limit = self._validate_distance(
            self.get_max_distance_meters()
            if max_distance_meters is None
            else max_distance_meters
        )
        profiles = repository.list_active_shift_profiles()
        candidates = repository.list_active_route_candidates()
        ordered = tuple(
            sorted(
                personnel,
                key=lambda item: (
                    0 if item.personnel_type == PersonelTuru.KADROLU else 1,
                    item.personnel_number,
                    item.id,
                ),
            )
        )
        reservations: dict[int, int] = {}
        results: list[AssignmentPreviewResult] = []
        shortage_sources: list[tuple[PersonnelSnapshot, str]] = []

        for person in ordered:
            base_reason = self._personnel_failure(person)
            if base_reason:
                results.append(self._failure(person, base_reason))
                continue
            matching_shift = tuple(item for item in profiles if item.shift == person.shift)
            if not matching_shift:
                results.append(self._failure(person, "Vardiyaya uygun aktif servis bulunamadı."))
                continue
            matching_collar = tuple(item for item in matching_shift if item.collar == person.collar)
            if not matching_collar:
                results.append(self._failure(person, "Yaka türü eşleşen aktif servis bulunamadı."))
                continue
            profile_ids = {item.shift_id for item in matching_collar}
            route_sources = tuple(item for item in candidates if item.shift_id in profile_ids)
            evaluated = self._evaluate_candidates(person, route_sources)
            if not evaluated:
                results.append(self._failure(person, "Aktif ve geçerli kayıtlı rota bulunamadı."))
                continue
            within_limit = tuple(
                item for item in evaluated if item.nearest.distance_meters <= limit
            )
            if not within_limit:
                closest = min(evaluated, key=lambda item: item.nearest.distance_meters)
                results.append(
                    self._failure(
                        person,
                        f"En yakın rota {closest.nearest.distance_meters:.0f} m; "
                        f"{limit:.0f} m yakınlık sınırı aşıldı.",
                        distance=closest.nearest.distance_meters,
                    )
                )
                continue
            ordered_candidates = sorted(within_limit, key=self._candidate_rank)
            available = [
                item
                for item in ordered_candidates
                if item.source.occupancy + reservations.get(item.source.shift_id, 0)
                < item.source.capacity
            ]
            if not available:
                suggestion = ordered_candidates[0]
                results.append(
                    self._failure(
                        person,
                        "Uygun servis-vardiya kapasitesi dolu.",
                        distance=suggestion.nearest.distance_meters,
                    )
                )
                shortage_sources.append((person, suggestion.source.route_name))
                continue

            forced_relation = manual_choices.get(person.id)
            chosen = next(
                (item for item in available if item.source.relation_id == forced_relation),
                available[0],
            )
            reserved_before = reservations.get(chosen.source.shift_id, 0)
            occupancy_before = chosen.source.occupancy + reserved_before
            reservations[chosen.source.shift_id] = reserved_before + 1
            occupancy_after = occupancy_before + 1
            alternatives = tuple(
                self._alternative(
                    item,
                    reservations.get(item.source.shift_id, 0),
                )
                for item in available
                if item.source.relation_id != chosen.source.relation_id
            )
            results.append(
                AssignmentPreviewResult(
                    personnel_id=person.id,
                    personnel_number=person.personnel_number,
                    personnel_name=person.name,
                    personnel_type=person.personnel_type.value,
                    shift=person.shift.value,
                    collar=person.collar.value,
                    personnel_latitude=person.latitude,
                    personnel_longitude=person.longitude,
                    service_code=chosen.source.service_code,
                    route_id=chosen.source.route_id,
                    relation_id=chosen.source.relation_id,
                    route_name=chosen.source.route_name,
                    route_geometry=chosen.geometry,
                    capacity=chosen.source.capacity,
                    occupancy_before=occupancy_before,
                    occupancy_after=occupancy_after,
                    remaining_capacity=chosen.source.capacity - occupancy_after,
                    distance_meters=chosen.nearest.distance_meters,
                    pickup_latitude=chosen.nearest.latitude,
                    pickup_longitude=chosen.nearest.longitude,
                    status="UYGUN",
                    reason="Önizleme önerisi; kalıcı atama yapılmadı.",
                    alternatives=alternatives,
                )
            )

        return AssignmentPreview(
            method=method,
            personnel_ids=tuple(item.id for item in ordered),
            max_distance_meters=limit,
            results=tuple(results),
            capacity_needs=self._capacity_summary(shortage_sources),
        )

    def _evaluate_candidates(
        self,
        person: PersonnelSnapshot,
        candidates: tuple[RouteCandidateSnapshot, ...],
    ) -> tuple[_EvaluatedCandidate, ...]:
        assert person.latitude is not None and person.longitude is not None
        result: list[_EvaluatedCandidate] = []
        for candidate in candidates:
            try:
                geometry = json.loads(candidate.geometry_json)
                coordinates = coordinates_from_geojson(geometry)
                nearest = nearest_point_on_linestring(
                    person.latitude,
                    person.longitude,
                    coordinates,
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            result.append(_EvaluatedCandidate(candidate, geometry, nearest))
        return tuple(result)

    @staticmethod
    def _candidate_rank(item: _EvaluatedCandidate) -> tuple[float, int, int, str, str, int]:
        source = item.source
        remaining = source.capacity - source.occupancy
        return (
            round(item.nearest.distance_meters, 6),
            -source.capacity,
            -remaining,
            source.service_code,
            source.route_name,
            source.relation_id,
        )

    def _personnel_failure(self, person: PersonnelSnapshot) -> str | None:
        if not person.active:
            return "Personel aktif değil."
        if person.has_active_assignment:
            return "Personelin zaten aktif bir servis ataması var."
        if person.latitude is None or person.longitude is None:
            return "Personel koordinatı eksik."
        if person.personnel_type == PersonelTuru.STAJYER:
            today = self.evaluation_date or date.today()
            if (
                person.internship_start is None
                or person.internship_end is None
                or person.internship_start > today
                or person.internship_end < today
            ):
                return "Stajyer tarih aralığı eksik veya geçersiz."
        return None

    @staticmethod
    def _alternative(
        item: _EvaluatedCandidate,
        reserved_count: int,
    ) -> AssignmentAlternative:
        source = item.source
        occupancy = source.occupancy + reserved_count
        return AssignmentAlternative(
            relation_id=source.relation_id,
            route_id=source.route_id,
            shift_id=source.shift_id,
            service_code=source.service_code,
            route_name=source.route_name,
            distance_meters=item.nearest.distance_meters,
            capacity=source.capacity,
            occupancy=occupancy,
            remaining_capacity=max(0, source.capacity - occupancy),
        )

    @staticmethod
    def _failure(
        person: PersonnelSnapshot,
        reason: str,
        *,
        distance: float | None = None,
    ) -> AssignmentPreviewResult:
        return AssignmentPreviewResult(
            personnel_id=person.id,
            personnel_number=person.personnel_number,
            personnel_name=person.name,
            personnel_type=person.personnel_type.value,
            shift=person.shift.value,
            collar=person.collar.value,
            personnel_latitude=person.latitude,
            personnel_longitude=person.longitude,
            service_code=None,
            route_id=None,
            relation_id=None,
            route_name=None,
            route_geometry=None,
            capacity=None,
            occupancy_before=None,
            occupancy_after=None,
            remaining_capacity=None,
            distance_meters=distance,
            pickup_latitude=None,
            pickup_longitude=None,
            status="ATANAMADI",
            reason=reason,
        )

    @staticmethod
    def _capacity_summary(
        sources: list[tuple[PersonnelSnapshot, str]],
    ) -> tuple[CapacityNeedSummary, ...]:
        grouped: dict[tuple[str, str], list[tuple[PersonnelSnapshot, str]]] = {}
        for person, route_name in sources:
            grouped.setdefault((person.shift.value, person.collar.value), []).append(
                (person, route_name)
            )
        summaries: list[CapacityNeedSummary] = []
        for (shift, collar), items in sorted(grouped.items()):
            areas: dict[str, int] = {}
            routes: dict[str, int] = {}
            for person, route_name in items:
                area = " / ".join(
                    value for value in (person.district, person.neighborhood) if value
                ) or "Konum bilgisi yok"
                areas[area] = areas.get(area, 0) + 1
                routes[route_name] = routes.get(route_name, 0) + 1
            area_text = ", ".join(
                f"{name} ({count})"
                for name, count in sorted(areas.items(), key=lambda item: (-item[1], item[0]))[:3]
            )
            route_text = sorted(routes.items(), key=lambda item: (-item[1], item[0]))[0][0]
            summaries.append(
                CapacityNeedSummary(
                    shift=shift,
                    collar=collar,
                    personnel_count=len(items),
                    needed_capacity=len(items),
                    concentrated_areas=area_text,
                    suggested_route_or_area=route_text,
                )
            )
        return tuple(summaries)

    @staticmethod
    def _validate_distance(value: float) -> float:
        try:
            distance = float(value)
        except (TypeError, ValueError) as error:
            raise AssignmentPreviewError("Yakınlık sınırı sayısal olmalıdır.") from error
        if not 1 <= distance <= 100_000:
            raise AssignmentPreviewError("Yakınlık sınırı 1-100000 metre arasında olmalıdır.")
        return distance
