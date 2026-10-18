"""Açık adreslerden sokak ve cadde alanlarını ayırma kuralları."""

from __future__ import annotations

import re
from dataclasses import dataclass

from normalization.text import normalize_text


_WORD = r"[\wÇĞİÖŞÜçğıiöşü'()-]+\.?"
_STREET_TYPE = r"(?:SK\.?|SOK\.?|SOKAK|SOKAĞI)"
_AVENUE_TYPE = r"(?:CD\.?|CAD\.?|CADDE|CADDESİ|BLV\.?|BULVAR|BULVARI)"
_NEIGHBORHOOD_PREFIX = re.compile(
    r"\b(?:MAH(?:ALLE(?:Sİ)?)?|MH)\.?\s+",
    re.IGNORECASE,
)
_STREET_PATTERN = re.compile(
    rf"(?P<road>(?:{_WORD}\s+){{0,3}}{_WORD}"
    rf"(?:\s+|(?<=[\d).])){_STREET_TYPE})\.?",
    re.IGNORECASE,
)
_AVENUE_PATTERN = re.compile(
    rf"(?P<road>(?:{_WORD}\s+){{0,3}}{_WORD}"
    rf"(?:\s+|(?<=[\d).])){_AVENUE_TYPE})\.?",
    re.IGNORECASE,
)
_STREET_TYPE_AT_END = re.compile(rf"{_STREET_TYPE}\.?$", re.IGNORECASE)
_AVENUE_TYPE_AT_END = re.compile(rf"{_AVENUE_TYPE}\.?$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class AddressRoadParts:
    street: str | None = None
    avenue: str | None = None


def _address_body(address: str) -> str:
    marker = _NEIGHBORHOOD_PREFIX.search(address)
    return address[marker.end() :] if marker is not None else address


def _canonical_street(value: str) -> str:
    match = _STREET_TYPE_AT_END.search(value)
    if match is None:
        return value.strip(" .")
    prefix = value[: match.start()].rstrip()
    return f"{prefix} Sokak".strip()


def _canonical_avenue(value: str) -> str:
    match = _AVENUE_TYPE_AT_END.search(value)
    if match is None:
        return value.strip(" .")
    road_type = match.group(0).rstrip(".").casefold()
    suffix = "Bulvarı" if road_type in {"blv", "bulvar", "bulvarı"} else "Caddesi"
    prefix = value[: match.start()].rstrip()
    return f"{prefix} {suffix}".strip()


def extract_address_roads(address: object) -> AddressRoadParts:
    """Açık adresteki ilk sokak ve ilk cadde/bulvar ifadesini ayırır."""
    normalized = normalize_text(address)
    if normalized is None:
        return AddressRoadParts()
    body = _address_body(normalized)
    street_match = _STREET_PATTERN.search(body)
    avenue_match = _AVENUE_PATTERN.search(body)
    if (
        street_match is not None
        and avenue_match is not None
        and avenue_match.start() < street_match.end()
        and street_match.end() < avenue_match.end()
    ):
        avenue_match = _AVENUE_PATTERN.search(body, street_match.end())
    if (
        street_match is not None
        and avenue_match is not None
        and street_match.start() < avenue_match.end()
        and avenue_match.end() < street_match.end()
    ):
        street_match = _STREET_PATTERN.search(body, avenue_match.end())
    street = (
        _canonical_street(street_match.group("road"))
        if street_match is not None
        else None
    )
    avenue = (
        _canonical_avenue(avenue_match.group("road"))
        if avenue_match is not None
        else None
    )
    return AddressRoadParts(street=street, avenue=avenue)


def extract_street(address: object) -> str | None:
    return extract_address_roads(address).street


def extract_avenue(address: object) -> str | None:
    return extract_address_roads(address).avenue


def classify_road(value: object) -> AddressRoadParts:
    """Nominatim'in tek road değerini sokak veya cadde alanına ayırır."""
    return extract_address_roads(value)
