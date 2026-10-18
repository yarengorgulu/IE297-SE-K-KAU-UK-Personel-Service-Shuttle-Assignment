"""Personel servislerinin ortak ve sabit fabrika varış noktası."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace


FACTORY_DESTINATION_SETTING_KEY = "fabrika_varis_noktasi"


@dataclass(frozen=True, slots=True)
class FactoryDestination:
    name: str
    address: str
    city: str
    district: str
    neighborhood: str
    latitude: float
    longitude: float

    def with_coordinates(
        self,
        latitude: float,
        longitude: float,
    ) -> "FactoryDestination":
        return replace(self, latitude=latitude, longitude=longitude)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, value: str) -> "FactoryDestination":
        payload = json.loads(value)
        if not isinstance(payload, dict):
            raise ValueError("Fabrika varış noktası ayarı geçersiz.")
        destination = cls(
            name=str(payload["name"]),
            address=str(payload["address"]),
            city=str(payload["city"]),
            district=str(payload["district"]),
            neighborhood=str(payload["neighborhood"]),
            latitude=float(payload["latitude"]),
            longitude=float(payload["longitude"]),
        )
        if not (-90 <= destination.latitude <= 90):
            raise ValueError("Fabrika enlemi -90 ile 90 arasında olmalıdır.")
        if not (-180 <= destination.longitude <= 180):
            raise ValueError("Fabrika boylamı -180 ile 180 arasında olmalıdır.")
        return destination


# Resmî merkez adresi: Yunusoğlu Mah. Yunusoğlu Sok. 177/A-177/B, Tarsus/Mersin.
# Koordinat, bu adresle eşleşen yayımlanmış Seçil Kauçuk işletme pinidir ve arayüzden
# düzeltilebilir; ayar değiştiğinde bütün hesaplanmış rotalar güncelliğini kaybeder.
DEFAULT_FACTORY_DESTINATION = FactoryDestination(
    name="Seçil Kauçuk",
    address="Yunusoğlu Mahallesi Yunusoğlu Sokak No:177/A-177/B",
    city="MERSİN",
    district="TARSUS",
    neighborhood="YUNUSOĞLU MAHALLESİ",
    latitude=36.94375678451021,
    longitude=34.97910084152655,
)
