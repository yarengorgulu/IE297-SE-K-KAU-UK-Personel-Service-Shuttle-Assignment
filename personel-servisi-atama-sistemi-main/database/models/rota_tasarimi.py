"""Aşama 11 kayıtlı rota, kontrol noktası ve servis-vardiya ilişkileri."""

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

if TYPE_CHECKING:
    from database.models.servis import ServisVardiyasi


class Rota(ActiveMixin, TimestampMixin, Base):
    """Servis-vardiyadan bağımsız, yeniden kullanılabilir kayıtlı rota."""

    __tablename__ = "rota"
    __table_args__ = (
        CheckConstraint(
            "olusturma_yontemi IN ('MANUEL', 'OTOMATIK')",
            name="rota_olusturma_yontemi",
        ),
        CheckConstraint("toplam_mesafe_metre >= 0", name="rota_mesafe_pozitif"),
        CheckConstraint("toplam_sure_saniye >= 0", name="rota_sure_pozitif"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rota_adi: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    olusturma_yontemi: Mapped[str] = mapped_column(String(20), nullable=False)
    baslangic_adi: Mapped[str] = mapped_column(String(300), nullable=False)
    baslangic_enlem: Mapped[float] = mapped_column(Float, nullable=False)
    baslangic_boylam: Mapped[float] = mapped_column(Float, nullable=False)
    bitis_adi: Mapped[str] = mapped_column(String(300), nullable=False)
    bitis_adresi: Mapped[str | None] = mapped_column(String(1000))
    bitis_enlem: Mapped[float] = mapped_column(Float, nullable=False)
    bitis_boylam: Mapped[float] = mapped_column(Float, nullable=False)
    toplam_mesafe_metre: Mapped[float] = mapped_column(Float, nullable=False)
    toplam_sure_saniye: Mapped[float] = mapped_column(Float, nullable=False)
    geojson: Mapped[str] = mapped_column(Text, nullable=False)
    hesaplanma_tarihi: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    )

    kontrol_noktalari: Mapped[list[RotaKontrolNoktasi]] = relationship(
        back_populates="rota",
        cascade="all, delete-orphan",
        order_by="RotaKontrolNoktasi.sira_no",
    )
    servis_vardiya_baglantilari: Mapped[list[ServisVardiyaRota]] = relationship(
        back_populates="rota",
        cascade="all, delete-orphan",
    )


class RotaKontrolNoktasi(ActiveMixin, TimestampMixin, Base):
    __tablename__ = "rota_kontrol_noktasi"
    __table_args__ = (
        UniqueConstraint("rota_id", "sira_no", name="rota_kontrol_sira_no"),
        CheckConstraint("sira_no > 0", name="rota_kontrol_sira_pozitif"),
        CheckConstraint("enlem BETWEEN -90 AND 90", name="rota_kontrol_enlem"),
        CheckConstraint("boylam BETWEEN -180 AND 180", name="rota_kontrol_boylam"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rota_id: Mapped[int] = mapped_column(
        ForeignKey("rota.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sira_no: Mapped[int] = mapped_column(Integer, nullable=False)
    nokta_adi: Mapped[str | None] = mapped_column(String(300))
    acik_adres: Mapped[str | None] = mapped_column(String(1000))
    mahalle: Mapped[str | None] = mapped_column(String(200))
    sokak: Mapped[str | None] = mapped_column(String(300))
    cadde: Mapped[str | None] = mapped_column(String(300))
    ilce: Mapped[str | None] = mapped_column(String(100))
    il: Mapped[str | None] = mapped_column(String(100))
    enlem: Mapped[float] = mapped_column(Float, nullable=False)
    boylam: Mapped[float] = mapped_column(Float, nullable=False)
    koordinat_durumu: Mapped[str] = mapped_column(
        String(30), nullable=False, default="MANUEL", server_default="MANUEL"
    )

    rota: Mapped[Rota] = relationship(back_populates="kontrol_noktalari")


class ServisVardiyaRota(ActiveMixin, TimestampMixin, Base):
    __tablename__ = "servis_vardiya_rota"
    __table_args__ = (
        UniqueConstraint(
            "servis_vardiya_id", "rota_id", name="servis_vardiya_rota_benzersiz"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    servis_vardiya_id: Mapped[int] = mapped_column(
        ForeignKey("servis_vardiya.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rota_id: Mapped[int] = mapped_column(
        ForeignKey("rota.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vardiya_tanimi: Mapped[str] = mapped_column(String(50), nullable=False)
    yaka_turu: Mapped[str] = mapped_column(String(20), nullable=False)
    kapasite: Mapped[int] = mapped_column(Integer, nullable=False)

    rota: Mapped[Rota] = relationship(back_populates="servis_vardiya_baglantilari")
    servis_vardiyasi: Mapped[ServisVardiyasi] = relationship(
        back_populates="kayitli_rota_baglantilari"
    )
