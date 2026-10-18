"""Personel modeli."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, Float, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.models.base import ActiveMixin, Base, TimestampMixin
from database.models.enums import PersonelTuru, VardiyaTuru, YakaTuru, enum_type

if TYPE_CHECKING:
    from database.models.atama import Atama


class Personel(ActiveMixin, TimestampMixin, Base):
    __tablename__ = "personel"
    __table_args__ = (
        CheckConstraint(
            "enlem IS NULL OR enlem BETWEEN -90 AND 90",
            name="enlem_araligi",
        ),
        CheckConstraint(
            "boylam IS NULL OR boylam BETWEEN -180 AND 180",
            name="boylam_araligi",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sicil_no: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    ad: Mapped[str | None] = mapped_column(String(100))
    soyad: Mapped[str | None] = mapped_column(String(100))
    ad_soyad: Mapped[str | None] = mapped_column(String(200))
    acik_adres: Mapped[str] = mapped_column(String(1000), nullable=False)
    il: Mapped[str | None] = mapped_column(String(100))
    ilce: Mapped[str | None] = mapped_column(String(100))
    mahalle: Mapped[str | None] = mapped_column(String(200))
    sokak: Mapped[str | None] = mapped_column(String(300))
    cadde: Mapped[str | None] = mapped_column(String(300))
    enlem: Mapped[float | None] = mapped_column(Float)
    boylam: Mapped[float | None] = mapped_column(Float)
    koordinat_durumu: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="EKSIK",
        server_default="EKSIK",
    )
    koordinat_adres_ozeti: Mapped[str | None] = mapped_column(String(64))
    vardiya_turu: Mapped[VardiyaTuru] = mapped_column(
        enum_type(VardiyaTuru, "personel_vardiya_turu"),
        nullable=False,
    )
    yaka_turu: Mapped[YakaTuru] = mapped_column(
        enum_type(YakaTuru, "personel_yaka_turu"),
        nullable=False,
    )
    personel_turu: Mapped[PersonelTuru] = mapped_column(
        enum_type(PersonelTuru, "personel_turu"),
        nullable=False,
    )
    aktarim_grup_kodu: Mapped[str | None] = mapped_column(String(100), index=True)
    staj_baslangic_tarihi: Mapped[date | None] = mapped_column(Date)
    staj_bitis_tarihi: Mapped[date | None] = mapped_column(Date)

    atamalar: Mapped[list[Atama]] = relationship(back_populates="personel")
