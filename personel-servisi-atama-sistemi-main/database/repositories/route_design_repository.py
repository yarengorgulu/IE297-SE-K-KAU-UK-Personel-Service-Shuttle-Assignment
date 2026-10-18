"""Kayıtlı rota tasarımı için veri erişim işlemleri."""

from __future__ import annotations

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from database.models import (
    Atama,
    Rota,
    RotaKontrolNoktasi,
    Servis,
    ServisVardiyasi,
    ServisVardiyaRota,
)
from database.repositories.base import BaseRepository


class SavedRouteRepository(BaseRepository[Rota]):
    model = Rota

    def get_detail(self, route_id: int) -> Rota | None:
        return self.session.scalar(
            select(Rota)
            .where(Rota.id == route_id)
            .options(
                selectinload(Rota.kontrol_noktalari),
                selectinload(Rota.servis_vardiya_baglantilari)
                .selectinload(ServisVardiyaRota.servis_vardiyasi)
                .selectinload(ServisVardiyasi.servis),
            )
        )

    def search(self, text: str = "", *, include_inactive: bool = True) -> list[Rota]:
        statement = select(Rota).options(
            selectinload(Rota.kontrol_noktalari),
            selectinload(Rota.servis_vardiya_baglantilari)
            .selectinload(ServisVardiyaRota.servis_vardiyasi)
            .selectinload(ServisVardiyasi.servis),
        )
        query = text.strip()
        if query:
            statement = statement.where(Rota.rota_adi.contains(query, autoescape=True))
        if not include_inactive:
            statement = statement.where(Rota.aktif_mi.is_(True))
        return list(self.session.scalars(statement.order_by(Rota.rota_adi, Rota.id)).unique())

    def replace_points(
        self,
        route: Rota,
        points: list[RotaKontrolNoktasi],
    ) -> None:
        route.kontrol_noktalari.clear()
        self.session.flush()
        route.kontrol_noktalari.extend(points)
        self.session.flush()

    def replace_shift_links(self, route: Rota, shift_ids: tuple[int, ...]) -> None:
        requested = tuple(dict.fromkeys(shift_ids))
        existing = {
            link.servis_vardiya_id: link
            for link in route.servis_vardiya_baglantilari
        }
        for link in existing.values():
            link.aktif_mi = link.servis_vardiya_id in requested
        for shift_id in requested:
            shift = self.session.get(ServisVardiyasi, shift_id)
            if shift is None:
                raise ValueError("Servis-vardiya kaydı bulunamadı.")
            link = existing.get(shift_id)
            if link is None:
                link = ServisVardiyaRota(servis_vardiya_id=shift_id)
                route.servis_vardiya_baglantilari.append(link)
            link.aktif_mi = True
            link.vardiya_tanimi = shift.vardiya_turu.value
            link.yaka_turu = shift.yaka_turu.value
            link.kapasite = shift.kapasite
        self.session.flush()

    def historical_assignment_count(self, route_id: int) -> int:
        statement = (
            select(func.count(Atama.id))
            .select_from(Atama)
            .outerjoin(
                ServisVardiyaRota,
                ServisVardiyaRota.servis_vardiya_id == Atama.servis_vardiya_id,
            )
            .where(
                or_(
                    Atama.rota_id == route_id,
                    and_(
                        Atama.rota_id.is_(None),
                        ServisVardiyaRota.rota_id == route_id,
                    ),
                )
            )
        )
        return int(self.session.scalar(statement) or 0)

    def delete_physical(self, route: Rota) -> None:
        self.session.delete(route)
        self.session.flush()


class RouteControlPointRepository(BaseRepository[RotaKontrolNoktasi]):
    model = RotaKontrolNoktasi


class ServiceRouteLinkRepository(BaseRepository[ServisVardiyaRota]):
    model = ServisVardiyaRota


def service_historical_assignment_count(session: Session, service_id: int) -> int:
    return int(
        session.scalar(
            select(func.count(Atama.id))
            .select_from(Atama)
            .join(ServisVardiyasi, ServisVardiyasi.id == Atama.servis_vardiya_id)
            .where(ServisVardiyasi.servis_id == service_id)
        )
        or 0
    )


def delete_unused_service(session: Session, service: Servis) -> None:
    """FK sırasını açık tutarak yalnızca kullanılmamış servisi fiziksel siler."""
    shift_ids = tuple(item.id for item in service.vardiyalar)
    if shift_ids:
        session.execute(
            delete(ServisVardiyaRota).where(
                ServisVardiyaRota.servis_vardiya_id.in_(shift_ids)
            )
        )
    session.delete(service)
    session.flush()
