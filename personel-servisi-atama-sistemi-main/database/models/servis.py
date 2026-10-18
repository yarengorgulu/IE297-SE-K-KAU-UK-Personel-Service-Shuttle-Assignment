"""Şoför, fiziksel servis, servis vardiyası ve güzergâh modelleri."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.models.base import ActiveMixin, Base, TimestampMixin
from database.models.enums import VardiyaTuru, YakaTuru, enum_type

if TYPE_CHECKING:
    from database.models.atama import Atama
    from database.models.rota_tasarimi import ServisVardiyaRota


class Sofor(ActiveMixin, TimestampMixin, Base):
    __tablename__ = "sofor"

    id: Mapped[int] = mapped_column(primary_key=True)
    ad: Mapped[str | None] = mapped_column(String(100))
    soyad: Mapped[str | None] = mapped_column(String(100))
    ad_soyad: Mapped[str] = mapped_column(String(200), nullable=False)
    telefon: Mapped[str | None] = mapped_column(String(30))

    servisler: Mapped[list[Servis]] = relationship(back_populates="sofor")


class Servis(ActiveMixin, TimestampMixin, Base):
    __tablename__ = "servis"

    id: Mapped[int] = mapped_column(primary_key=True)
    servis_kodu: Mapped[str] = mapped_column(String(30), nullable=False, unique=True, index=True)
    rota_adi: Mapped[str | None] = mapped_column(String(300))
    sofor_id: Mapped[int | None] = mapped_column(
        ForeignKey("sofor.id", ondelete="SET NULL"),
        index=True,
    )

    sofor: Mapped[Sofor | None] = relationship(back_populates="servisler")
    vardiyalar: Mapped[list[ServisVardiyasi]] = relationship(
        back_populates="servis",
        cascade="all, delete-orphan",
    )


class ServisVardiyasi(ActiveMixin, TimestampMixin, Base):
    __tablename__ = "servis_vardiya"
    __table_args__ = (
        UniqueConstraint(
            "servis_id",
            "vardiya_turu",
            "yaka_turu",
            name="servis_vardiya_yaka",
        ),
        CheckConstraint("kapasite > 0", name="pozitif_kapasite"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    servis_id: Mapped[int] = mapped_column(
        ForeignKey("servis.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vardiya_turu: Mapped[VardiyaTuru] = mapped_column(
        enum_type(VardiyaTuru, "servis_vardiya_turu"),
        nullable=False,
    )
    yaka_turu: Mapped[YakaTuru] = mapped_column(
        enum_type(YakaTuru, "servis_vardiya_yaka_turu"),
        nullable=False,
    )
    kapasite: Mapped[int] = mapped_column(Integer, nullable=False)
    rota_adi: Mapped[str | None] = mapped_column(String(300))

    servis: Mapped[Servis] = relationship(back_populates="vardiyalar")
    atamalar: Mapped[list[Atama]] = relationship(back_populates="servis_vardiyasi")
    guzergah_noktalari: Mapped[list[ServisGuzergahNoktasi]] = relationship(
        back_populates="servis_vardiyasi",
        cascade="all, delete-orphan",
        order_by="ServisGuzergahNoktasi.sira_no",
    )
    guzergah_geometrisi: Mapped[ServisGuzergahGeometrisi | None] = relationship(
        back_populates="servis_vardiyasi",
        cascade="all, delete-orphan",
        uselist=False,
    )
    kayitli_rota_baglantilari: Mapped[list[ServisVardiyaRota]] = relationship(
        back_populates="servis_vardiyasi",
        cascade="all, delete-orphan",
    )


class ServisGuzergahNoktasi(ActiveMixin, TimestampMixin, Base):
    __tablename__ = "servis_guzergah_noktasi"
    __table_args__ = (
        UniqueConstraint(
            "servis_vardiya_id",
            "sira_no",
            name="servis_vardiya_sira_no",
        ),
        CheckConstraint("sira_no > 0", name="pozitif_sira_no"),
        CheckConstraint(
            "enlem IS NULL OR enlem BETWEEN -90 AND 90",
            name="enlem_araligi",
        ),
        CheckConstraint(
            "boylam IS NULL OR boylam BETWEEN -180 AND 180",
            name="boylam_araligi",
        ),
        CheckConstraint(
            "ilce IS NOT NULL OR mahalle IS NOT NULL OR nokta_adi IS NOT NULL",
            name="konum_tanimi_zorunlu",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    servis_vardiya_id: Mapped[int] = mapped_column(
        ForeignKey("servis_vardiya.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sira_no: Mapped[int] = mapped_column(Integer, nullable=False)
    nokta_adi: Mapped[str | None] = mapped_column(String(300))
    acik_adres: Mapped[str | None] = mapped_column(String(1000))
    aciklama: Mapped[str | None] = mapped_column(Text)
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
    durak_mi: Mapped[bool] = mapped_column(nullable=False, default=False)

    servis_vardiyasi: Mapped[ServisVardiyasi] = relationship(
        back_populates="guzergah_noktalari"
    )


class ServisGuzergahGeometrisi(ActiveMixin, TimestampMixin, Base):
    __tablename__ = "servis_guzergah_geometrisi"
    __table_args__ = (
        CheckConstraint(
            "toplam_mesafe_metre >= 0",
            name="negatif_olmayan_mesafe",
        ),
        CheckConstraint(
            "toplam_sure_saniye >= 0",
            name="negatif_olmayan_sure",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    servis_vardiya_id: Mapped[int] = mapped_column(
        ForeignKey("servis_vardiya.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    geojson: Mapped[str] = mapped_column(Text, nullable=False)
    toplam_mesafe_metre: Mapped[float] = mapped_column(Float, nullable=False)
    toplam_sure_saniye: Mapped[float] = mapped_column(Float, nullable=False)
    nokta_ozeti: Mapped[str] = mapped_column(String(64), nullable=False)
    guncel_mi: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )
    hesaplanma_tarihi: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    )

    servis_vardiyasi: Mapped[ServisVardiyasi] = relationship(
        back_populates="guzergah_geometrisi"
    )
