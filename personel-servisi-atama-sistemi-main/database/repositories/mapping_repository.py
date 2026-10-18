"""Adres çözümleme ve servis-vardiya rota sonuçları veri erişimi."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from database.models import (
    AdresOnbellegi,
    Personel,
    ServisGuzergahGeometrisi,
    ServisGuzergahNoktasi,
    ServisVardiyasi,
    UygulamaAyari,
)
from database.repositories.base import BaseRepository


class AddressCacheRepository(BaseRepository[AdresOnbellegi]):
    model = AdresOnbellegi

    def get_by_normalized_address(self, normalized_address: str) -> AdresOnbellegi | None:
        return self.session.scalar(
            select(AdresOnbellegi).where(
                AdresOnbellegi.normalize_adres == normalized_address
            )
        )


class ApplicationSettingRepository(BaseRepository[UygulamaAyari]):
    model = UygulamaAyari

    def get_value(self, key: str) -> str | None:
        setting = self.session.get(UygulamaAyari, key)
        return None if setting is None else setting.deger

    def set_value(self, key: str, value: str) -> UygulamaAyari:
        setting = self.session.get(UygulamaAyari, key)
        if setting is None:
            setting = self.add(UygulamaAyari(anahtar=key, deger=value))
        else:
            setting.deger = value
            self.session.flush()
        return setting


class PersonnelCoordinateRepository(BaseRepository[Personel]):
    model = Personel

    def list_missing_or_stale(self) -> list[Personel]:
        statement = (
            select(Personel)
            .where(
                (Personel.enlem.is_(None))
                | (Personel.boylam.is_(None))
                | (Personel.koordinat_durumu.in_(("EKSIK", "BULUNAMADI", "GUNCEL_DEGIL")))
            )
            .order_by(Personel.sicil_no)
        )
        return list(self.session.scalars(statement))


class RoutePointCoordinateRepository(BaseRepository[ServisGuzergahNoktasi]):
    model = ServisGuzergahNoktasi

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

    def list_missing_or_stale(self) -> list[ServisGuzergahNoktasi]:
        statement = (
            select(ServisGuzergahNoktasi)
            .where(
                (ServisGuzergahNoktasi.enlem.is_(None))
                | (ServisGuzergahNoktasi.boylam.is_(None))
                | (
                    ServisGuzergahNoktasi.koordinat_durumu.in_(
                        ("EKSIK", "BULUNAMADI", "GUNCEL_DEGIL")
                    )
                )
            )
            .order_by(
                ServisGuzergahNoktasi.servis_vardiya_id,
                ServisGuzergahNoktasi.sira_no,
            )
        )
        return list(self.session.scalars(statement))


class RouteGeometryRepository(BaseRepository[ServisGuzergahGeometrisi]):
    model = ServisGuzergahGeometrisi

    def get_for_shift(self, shift_id: int) -> ServisGuzergahGeometrisi | None:
        return self.session.scalar(
            select(ServisGuzergahGeometrisi).where(
                ServisGuzergahGeometrisi.servis_vardiya_id == shift_id
            )
        )

    def mark_stale(self, shift_id: int) -> None:
        geometry = self.get_for_shift(shift_id)
        if geometry is not None:
            geometry.guncel_mi = False

    def mark_all_stale(self) -> None:
        for geometry in self.list_all(include_inactive=True):
            geometry.guncel_mi = False
