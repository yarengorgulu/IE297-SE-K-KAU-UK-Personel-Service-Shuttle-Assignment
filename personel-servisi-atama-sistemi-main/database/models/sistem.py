"""Adres önbelleği ve uygulama ayarları modelleri."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, TimestampMixin


class AdresOnbellegi(TimestampMixin, Base):
    __tablename__ = "adres_onbellek"
    __table_args__ = (
        CheckConstraint("enlem BETWEEN -90 AND 90", name="enlem_araligi"),
        CheckConstraint("boylam BETWEEN -180 AND 180", name="boylam_araligi"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    normalize_adres: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
        unique=True,
        index=True,
    )
    orijinal_adres: Mapped[str] = mapped_column(String(1000), nullable=False)
    enlem: Mapped[float] = mapped_column(Float, nullable=False)
    boylam: Mapped[float] = mapped_column(Float, nullable=False)
    kaynak: Mapped[str] = mapped_column(String(100), nullable=False)


class UygulamaAyari(TimestampMixin, Base):
    __tablename__ = "ayar"

    anahtar: Mapped[str] = mapped_column(String(200), primary_key=True)
    deger: Mapped[str] = mapped_column(Text, nullable=False)
