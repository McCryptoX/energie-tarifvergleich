DOMAIN = "energy_tariff_compare"
PLAT_NAME = "Energie & Tarifvergleich"
CONF_PATH = "energy_tariff_compare/tariffs.yaml"
DB_PATH = "energy_tariff_compare/data/energy.sqlite"
TZ_NAME = "Europe/Berlin"

TARIFF_IDS = (
    "octopus_heat",
    "octopus_heat_loyalty",
    "fix_tarif",
    "dynamic",
    "dynamic_modul3",
)

REFERENCE_ID = "octopus_heat"

# Leftover unique_ids after a tariff-id rename. Value is the new unique_id.
PRICE_UNIQUE_ID_MIGRATIONS = {
    "etc_price_naturwerke_fix": "etc_price_fix_tarif",
}
PRICE_ENTITY_IDS = {
    "etc_price_fix_tarif": "sensor.tarifvergleich_preis_fix",
}


def leftover_unique_id_action(
    old_entity_id: str | None, new_entity_id: str | None
) -> str:
    """How to treat a renamed price-sensor unique_id: skip, retarget, remove_old."""
    if not old_entity_id:
        return "skip"
    if not new_entity_id:
        return "retarget"
    if old_entity_id == new_entity_id:
        return "skip"
    return "remove_old"
