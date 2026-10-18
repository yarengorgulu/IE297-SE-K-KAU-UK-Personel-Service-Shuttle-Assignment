"""Personel ile vardiya bazlı servis kaydını ilişkilendiren atama modeli."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.models.base import ActiveMixin, Base, TimestampMixin
from database.models.enums import AtamaDurumu, enum_type

if TYPE_CHECKING:
    from database.models.personel import Personel
    from database.models.rota_tasarimi import Rota
    from database.models.servis import ServisVardiyasi


class Atama(ActiveMixin, TimestampMixin, Base):
    __tablename__ = "atama"
    __table_args__ = (
        CheckConstraint(
            "guzergaha_uzaklik_metre IS NULL OR guzergaha_uzaklik_metre >= 0",
            name="negatif_olmayan_uzaklik",
        ),
        CheckConstraint(
            "aktif_mi = 0 OR servis_vardiya_id IS NOT NULL",
            name="aktif_atamada_servis_zorunlu",
        ),
        Index(
            "uq_atama_personel_aktif",
            "personel_id",
            unique=True,
            sqlite_where=text("aktif_mi = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    personel_id: Mapped[int] = mapped_column(
        ForeignKey("personel.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    servis_vardiya_id: Mapped[int | None] = mapped_column(
        ForeignKey("servis_vardiya.id", ondelete="RESTRICT"),
        index=True,
    )
    rota_id: Mapped[int | None] = mapped_column(
        ForeignKey("rota.id", ondelete="RESTRICT"),
        index=True,
    )
    atama_tarihi: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    )
    guzergahta_en_yakin_nokta_enlem: Mapped[float | None] = mapped_column(Float)
    guzergahta_en_yakin_nokta_boylam: Mapped[float | None] = mapped_column(Float)
    guzergaha_uzaklik_metre: Mapped[float | None] = mapped_column(Float)
    atama_durumu: Mapped[AtamaDurumu] = mapped_column(
        enum_type(AtamaDurumu, "atama_durumu"),
        nullable=False,
    )
    atama_aciklamasi: Mapped[str | None] = mapped_column(Text)
    personel: Mapped[Personel] = relationship(back_populates="atamalar")
    servis_vardiyasi: Mapped[ServisVardiyasi | None] = relationship(
        back_populates="atamalar"
    )
    rota: Mapped[Rota | None] = relationship()
