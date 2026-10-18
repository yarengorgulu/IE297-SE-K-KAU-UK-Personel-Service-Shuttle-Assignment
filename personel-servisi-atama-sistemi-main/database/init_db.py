"""Veritabanı şemasının uygulama başlangıcında hazırlanması."""

from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect

from app.assignment_preview import (
    DEFAULT_PERSONNEL_ROUTE_MAX_DISTANCE_METERS,
    PERSONNEL_ROUTE_MAX_DISTANCE_SETTING_KEY,
)
from app.factory import DEFAULT_FACTORY_DESTINATION, FACTORY_DESTINATION_SETTING_KEY
from app.paths import DATABASE_PATH
from database.connection import engine
from database.models import (
    Base,
    ServisGuzergahGeometrisi,
    ServisGuzergahNoktasi,
)
from normalization.address import extract_address_roads


logger = logging.getLogger(__name__)


def _ensure_saved_route_link_columns(database_engine: Engine) -> None:
    """Erken Aşama 11 veritabanlarını ilişki anlık görüntü alanlarıyla tamamlar."""
    table_name = "servis_vardiya_rota"
    inspector = inspect(database_engine)
    if table_name not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    additions = {
        "vardiya_tanimi": "VARCHAR(50) NOT NULL DEFAULT ''",
        "yaka_turu": "VARCHAR(20) NOT NULL DEFAULT ''",
        "kapasite": "INTEGER NOT NULL DEFAULT 1",
    }
    missing = [name for name in additions if name not in columns]
    if not missing:
        return
    with database_engine.begin() as connection:
        for name in missing:
            connection.exec_driver_sql(
                f"ALTER TABLE {table_name} ADD COLUMN {name} {additions[name]}"
            )
        connection.exec_driver_sql(
            "UPDATE servis_vardiya_rota SET "
            "vardiya_tanimi = COALESCE((SELECT vardiya_turu FROM servis_vardiya "
            "WHERE servis_vardiya.id = servis_vardiya_rota.servis_vardiya_id), ''), "
            "yaka_turu = COALESCE((SELECT yaka_turu FROM servis_vardiya "
            "WHERE servis_vardiya.id = servis_vardiya_rota.servis_vardiya_id), ''), "
            "kapasite = COALESCE((SELECT kapasite FROM servis_vardiya "
            "WHERE servis_vardiya.id = servis_vardiya_rota.servis_vardiya_id), 1)"
        )
    logger.info("Kayıtlı rota servis-vardiya anlık görüntü alanları hazırlandı.")


def _ensure_assignment_route_column(database_engine: Engine) -> None:
    """Mevcut atamaları koruyarak seçilen kayıtlı rota bağını ekler."""
    table_name = "atama"
    inspector = inspect(database_engine)
    if table_name not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    with database_engine.begin() as connection:
        if "rota_id" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE atama ADD COLUMN rota_id INTEGER"
            )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_atama_rota_id ON atama (rota_id)"
        )


def _ensure_default_factory_destination(database_engine: Engine) -> None:
    """Eksik ayarı ekler; kullanıcının mevcut fabrika ayarını değiştirmez."""
    if "ayar" not in inspect(database_engine).get_table_names():
        return
    with database_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT OR IGNORE INTO ayar (anahtar, deger) VALUES (?, ?)",
            (
                FACTORY_DESTINATION_SETTING_KEY,
                DEFAULT_FACTORY_DESTINATION.to_json(),
            ),
        )


def _ensure_default_assignment_preview_settings(database_engine: Engine) -> None:
    """Atama önizlemesi yakınlık sınırını yalnızca eksikse ekler."""
    if "ayar" not in inspect(database_engine).get_table_names():
        return
    with database_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT OR IGNORE INTO ayar (anahtar, deger) VALUES (?, ?)",
            (
                PERSONNEL_ROUTE_MAX_DISTANCE_SETTING_KEY,
                str(DEFAULT_PERSONNEL_ROUTE_MAX_DISTANCE_METERS),
            ),
        )


def _ensure_coordinate_tracking_columns(
    database_engine: Engine,
    table_name: str,
) -> None:
    inspector = inspect(database_engine)
    if table_name not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    additions = []
    if "koordinat_durumu" not in columns:
        additions.append(
            "ADD COLUMN koordinat_durumu VARCHAR(30) NOT NULL DEFAULT 'EKSIK'"
        )
    if "koordinat_adres_ozeti" not in columns:
        additions.append("ADD COLUMN koordinat_adres_ozeti VARCHAR(64)")
    if not additions:
        return
    with database_engine.begin() as connection:
        for addition in additions:
            connection.exec_driver_sql(f"ALTER TABLE {table_name} {addition}")
        if {"enlem", "boylam"}.issubset(columns):
            connection.exec_driver_sql(
                f"UPDATE {table_name} SET koordinat_durumu = 'MEVCUT' "
                "WHERE enlem IS NOT NULL AND boylam IS NOT NULL "
                "AND koordinat_durumu = 'EKSIK'"
            )
    logger.info("Koordinat takip alanları eklendi: %s", table_name)


def _ensure_address_road_columns(database_engine: Engine, table_name: str) -> None:
    """Ayrı sokak/cadde alanlarını ekler ve mevcut adreslerden güvenle doldurur."""
    inspector = inspect(database_engine)
    if table_name not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    with database_engine.begin() as connection:
        for column_name in ("sokak", "cadde"):
            if column_name in columns:
                continue
            connection.exec_driver_sql(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} VARCHAR(300)"
            )
            columns.add(column_name)
        source_columns = [
            name for name in ("acik_adres", "sokak_cadde") if name in columns
        ]
        if not source_columns:
            return
        source_expression = ", ".join(source_columns)
        shift_column = (
            ", servis_vardiya_id"
            if "servis_vardiya_id" in columns
            else ""
        )
        rows = connection.exec_driver_sql(
            f"SELECT id, {source_expression}, sokak, cadde{shift_column} "
            f"FROM {table_name} WHERE sokak IS NULL OR cadde IS NULL"
        ).all()
        updates: list[dict[str, object]] = []
        affected_shift_ids: set[int] = set()
        for row in rows:
            road_offset = 1 + len(source_columns)
            extracted = [
                extract_address_roads(value)
                for value in row[1:road_offset]
            ]
            existing_street = row[road_offset]
            existing_avenue = row[road_offset + 1]
            street = existing_street or next(
                (parts.street for parts in extracted if parts.street),
                None,
            )
            avenue = existing_avenue or next(
                (parts.avenue for parts in extracted if parts.avenue),
                None,
            )
            if street == existing_street and avenue == existing_avenue:
                continue
            updates.append({"id": row[0], "sokak": street, "cadde": avenue})
            shift_index = road_offset + 2
            if shift_column and row[shift_index] is not None:
                affected_shift_ids.add(int(row[shift_index]))
        if updates:
            status_assignment = ""
            if {
                "enlem",
                "boylam",
                "koordinat_durumu",
            }.issubset(columns):
                status_assignment = (
                    ", koordinat_durumu = CASE "
                    "WHEN enlem IS NOT NULL AND boylam IS NOT NULL "
                    "THEN 'GUNCEL_DEGIL' ELSE koordinat_durumu END"
                )
            connection.exec_driver_sql(
                f"UPDATE {table_name} SET sokak = :sokak, cadde = :cadde"
                f"{status_assignment} WHERE id = :id",
                updates,
            )
        if affected_shift_ids:
            geometry_columns = {
                column["name"]
                for column in inspect(connection).get_columns(
                    "servis_guzergah_geometrisi"
                )
            }
            if {"servis_vardiya_id", "guncel_mi"}.issubset(geometry_columns):
                connection.exec_driver_sql(
                    "UPDATE servis_guzergah_geometrisi SET guncel_mi = 0 "
                    "WHERE servis_vardiya_id = :shift_id",
                    [{"shift_id": shift_id} for shift_id in affected_shift_ids],
                )
    logger.info(
        "Ayrı sokak/cadde alanları hazırlandı: tablo=%s, doldurulan=%s",
        table_name,
        len(updates),
    )


def _migrate_route_geometry_schema(database_engine: Engine) -> None:
    inspector = inspect(database_engine)
    table_name = "servis_guzergah_geometrisi"
    if table_name not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    current_columns = {
        "servis_vardiya_id",
        "toplam_sure_saniye",
        "nokta_ozeti",
        "guncel_mi",
    }
    if current_columns.issubset(columns):
        return

    backup_table = "servis_guzergah_geometrisi_legacy"
    with database_engine.begin() as connection:
        row_count = int(
            connection.exec_driver_sql(
                f"SELECT COUNT(*) FROM {table_name}"
            ).scalar_one()
        )
        if row_count:
            if backup_table in set(inspect(connection).get_table_names()):
                raise RuntimeError(
                    "Eski rota geometrisi için yedek tablo zaten mevcut. "
                    "Şema güncellemesi güvenli biçimde tamamlanamadı."
                )
            connection.exec_driver_sql(
                f"ALTER TABLE {table_name} RENAME TO {backup_table}"
            )
            for index_row in connection.exec_driver_sql(
                f"PRAGMA index_list('{backup_table}')"
            ).mappings():
                if index_row["origin"] == "c":
                    index_name = str(index_row["name"]).replace('"', '""')
                    connection.exec_driver_sql(f'DROP INDEX "{index_name}"')
            logger.warning(
                "Eski fiziksel-servis rota geometrisi %s tablosunda korundu; "
                "servis-vardiya ilişkisi belirsiz olduğu için taşınmadı.",
                backup_table,
            )
        else:
            connection.exec_driver_sql(f"DROP TABLE {table_name}")

    ServisGuzergahGeometrisi.__table__.create(database_engine, checkfirst=True)
    logger.info("Rota geometrisi şeması servis-vardiya bazına geçirildi.")


def _apply_compatibility_migrations(database_engine: Engine) -> None:
    """create_all tarafından güncellenmeyen mevcut SQLite tablolarını tamamlar."""
    inspector = inspect(database_engine)
    if "servis_vardiya" not in inspector.get_table_names():
        return
    column_names = {
        column["name"] for column in inspector.get_columns("servis_vardiya")
    }
    if "rota_adi" not in column_names:
        with database_engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE servis_vardiya ADD COLUMN rota_adi VARCHAR(300)"
            )
        logger.info("Veritabanı uyumluluk güncellemesi uygulandı: servis_vardiya.rota_adi")

    inspector = inspect(database_engine)
    if "personel" not in inspector.get_table_names():
        return
    personnel_columns = {
        column["name"] for column in inspector.get_columns("personel")
    }
    missing_name_columns = [
        column_name
        for column_name in ("ad", "soyad")
        if column_name not in personnel_columns
    ]
    if missing_name_columns:
        with database_engine.begin() as connection:
            for column_name in missing_name_columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE personel ADD COLUMN {column_name} VARCHAR(100)"
                )
        logger.info(
            "Veritabanı uyumluluk güncellemesi uygulandı: personel.%s",
            ", personel.".join(missing_name_columns),
        )

    inspector = inspect(database_engine)
    if "sofor" not in inspector.get_table_names():
        return
    driver_columns = {
        column["name"] for column in inspector.get_columns("sofor")
    }
    missing_driver_name_columns = [
        column_name
        for column_name in ("ad", "soyad")
        if column_name not in driver_columns
    ]
    if missing_driver_name_columns:
        with database_engine.begin() as connection:
            for column_name in missing_driver_name_columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE sofor ADD COLUMN {column_name} VARCHAR(100)"
                )
        logger.info(
            "Veritabanı uyumluluk güncellemesi uygulandı: sofor.%s",
            ", sofor.".join(missing_driver_name_columns),
        )

    _ensure_coordinate_tracking_columns(database_engine, "personel")
    _ensure_coordinate_tracking_columns(database_engine, "servis_guzergah_noktasi")
    _migrate_route_geometry_schema(database_engine)

    inspector = inspect(database_engine)
    if "servis_guzergah_noktasi" not in inspector.get_table_names():
        return
    route_columns = {
        column["name"]: column
        for column in inspector.get_columns("servis_guzergah_noktasi")
    }
    route_schema_is_current = (
        "servis_vardiya_id" in route_columns
        and "aciklama" in route_columns
        and route_columns.get("enlem", {}).get("nullable", False)
        and route_columns.get("boylam", {}).get("nullable", False)
    )
    if route_schema_is_current:
        return

    backup_table = "servis_guzergah_noktasi_legacy"
    with database_engine.begin() as connection:
        row_count = int(
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM servis_guzergah_noktasi"
            ).scalar_one()
        )
        if row_count:
            existing_tables = set(inspect(connection).get_table_names())
            if backup_table in existing_tables:
                raise RuntimeError(
                    "Eski güzergâh noktaları için yedek tablo zaten mevcut. "
                    "Şema güncellemesi güvenli biçimde tamamlanamadı."
                )
            connection.exec_driver_sql(
                f"ALTER TABLE servis_guzergah_noktasi RENAME TO {backup_table}"
            )
            for index_row in connection.exec_driver_sql(
                f"PRAGMA index_list('{backup_table}')"
            ).mappings():
                index_name = str(index_row["name"])
                if index_row["origin"] == "c":
                    quoted_name = index_name.replace('"', '""')
                    connection.exec_driver_sql(f'DROP INDEX "{quoted_name}"')
            logger.warning(
                "Eski güzergâh noktaları %s tablosunda korundu; vardiya ilişkisi "
                "belirsiz olduğu için otomatik eşleştirme yapılmadı.",
                backup_table,
            )
        else:
            connection.exec_driver_sql("DROP TABLE servis_guzergah_noktasi")

    ServisGuzergahNoktasi.__table__.create(database_engine, checkfirst=True)
    logger.info(
        "Veritabanı uyumluluk güncellemesi uygulandı: "
        "servis_guzergah_noktasi artık servis_vardiya kaydına bağlı."
    )


def initialize_database(database_engine: Engine = engine) -> None:
    """Tanımlı tüm SQLAlchemy tablolarını yoksa oluşturur."""
    Base.metadata.create_all(database_engine)
    _ensure_saved_route_link_columns(database_engine)
    _ensure_assignment_route_column(database_engine)
    _apply_compatibility_migrations(database_engine)
    _ensure_address_road_columns(database_engine, "personel")
    _ensure_address_road_columns(database_engine, "servis_guzergah_noktasi")
    _ensure_default_factory_destination(database_engine)
    _ensure_default_assignment_preview_settings(database_engine)
    logger.info("SQLAlchemy veritabanı şeması hazır: %s", DATABASE_PATH)
