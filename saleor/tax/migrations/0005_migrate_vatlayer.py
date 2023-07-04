# Generated by Django 3.2.14 on 2022-08-18 10:31

from typing import List, Optional
from django_countries import countries
from django.db import migrations


VATLAYER_ID = "mirumee.taxes.vatlayer"


# Must be the same as in 0004_migrate_tax_class.py
TAX_CLASS_ZERO_RATE = "No Taxes"


def _clear_country_code(country_code: str) -> Optional[str]:
    return countries.alpha2(country_code.strip()) if country_code else None


def _clear_str_list_country_codes(country_codes: str) -> List[str]:
    countries = [_clear_country_code(cc) for cc in country_codes.split(",")]
    return [cc for cc in countries if cc]


def create_tax_configurations(apps, vatlayer_configs):
    TaxConfigurationPerCountry = apps.get_model("tax", "TaxConfigurationPerCountry")

    # Map of countries to use origin country's tax, based on the
    # `countries_to_calculate_taxes_from_origin` setting. If a country code appears
    # more than once in the list, we override is with the last seen origin_country.
    use_origin_country_map = {}

    for vatlayer_config in vatlayer_configs:
        config_dict = {
            item["name"]: item["value"] for item in vatlayer_config.configuration
        }
        channel = vatlayer_config.channel

        # Migrate `countries_to_calculate_taxes_from_origin` setting.
        origin_country = _clear_country_code(config_dict.get("origin_country", ""))
        countries_to_calculate_taxes_from_origin = _clear_str_list_country_codes(
            config_dict.get("countries_to_calculate_taxes_from_origin", "")
        )
        if origin_country and countries_to_calculate_taxes_from_origin:
            for country in countries_to_calculate_taxes_from_origin:
                use_origin_country_map[country] = origin_country

        # Migrate `excluded_countries` to new tax configuration.
        excluded_countries = _clear_str_list_country_codes(
            config_dict.get("excluded_countries", "")
        )
        if excluded_countries:
            tax_configuration = channel.tax_configuration
            for country in excluded_countries:
                TaxConfigurationPerCountry.objects.update_or_create(
                    tax_configuration=tax_configuration,
                    country=country,
                    defaults={"charge_taxes": False},
                )

    return use_origin_country_map


def create_tax_rates(apps, use_origin_country_map):
    TaxClass = apps.get_model("tax", "TaxClass")
    TaxClassCountryRate = apps.get_model("tax", "TaxClassCountryRate")

    tax_classes = TaxClass.objects.exclude(name=TAX_CLASS_ZERO_RATE)

    # django_prices_vatlayer is removed in Saleor 3.15; if it's not installed, we're
    # skipping this part of the migration.
    try:
        VAT = apps.get_model("django_prices_vatlayer", "VAT")
    except LookupError:
        vat_rates = []
    else:
        vat_rates = VAT.objects.all()

    rates = {}
    for tax_class in tax_classes:
        for vat in vat_rates:
            # Collect standard rates to create
            standard_rate = TaxClassCountryRate(
                tax_class=tax_class,
                country=vat.country_code,
                rate=vat.data["standard_rate"],
            )
            rates[(tax_class.id, vat.country_code)] = standard_rate

            # Collect reduced rates to create
            if tax_class.name in vat.data["reduced_rates"]:
                reduced_rate = TaxClassCountryRate(
                    tax_class=tax_class,
                    country=vat.country_code,
                    rate=vat.data["reduced_rates"][tax_class.name],
                )
                rates[(tax_class.id, vat.country_code)] = reduced_rate

        # Swap rates for countries that should use origin country tax rate instead of
        # own rates.
        for country_code, origin in use_origin_country_map.items():
            country_rate_obj = rates.get((tax_class.id, country_code))
            origin_rate_obj = rates.get((tax_class.id, origin))
            if country_rate_obj and origin_rate_obj:
                country_rate_obj.rate = origin_rate_obj.rate
                rates[(tax_class.id, country_code)] = country_rate_obj

    TaxClassCountryRate.objects.bulk_create(rates.values())


def migrate_vatlayer(apps, _schema_editor):
    PluginConfiguration = apps.get_model("plugins", "PluginConfiguration")

    vatlayer_configs = PluginConfiguration.objects.filter(
        active=True, identifier=VATLAYER_ID
    )
    is_vatlayer_enabled = vatlayer_configs.exists()

    if is_vatlayer_enabled:
        use_origin_country_map = create_tax_configurations(apps, vatlayer_configs)
        create_tax_rates(apps, use_origin_country_map)


class Migration(migrations.Migration):
    dependencies = [
        ("tax", "0004_migrate_tax_classes"),
    ]

    operations = [migrations.RunPython(migrate_vatlayer, migrations.RunPython.noop)]