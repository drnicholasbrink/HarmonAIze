# ruff: noqa: I001
import os
from typing import Any

from django.conf import settings  # type: ignore[attr-defined]
from django.core.management.base import BaseCommand  # type: ignore[attr-defined]

from climate.models import ClimateDataSource, ClimateVariable, ClimateVariableMapping


class Command(BaseCommand):
    help = (
        "Initialize climate data services with available credentials and default API "
        "query parameters. Intended for startup use."
    )

    DEFAULT_SERVICE_CONFIG: dict[str, dict[str, Any]] = {
        "gee": {
            "name": "Google Earth Engine",
            "description": (
                "Google Earth Engine data access for gridded climate products."
            ),
            "api_endpoint": "https://earthengine.googleapis.com",
            "credential_env": "GOOGLE_APPLICATION_CREDENTIALS",
            # Parameters commonly applied to point extraction in GEE
            "parameters": {
                "reducer": "first",
                "maxPixels": 1e9,
            },
        },
    }

    CORE_VARIABLES: list[dict[str, Any]] = [
        {
            "name": "temperature_2m",
            "display_name": "2m Air Temperature",
            "description": "Air temperature at 2 meters above the surface",
            "category": "temperature",
            "unit": "degrees Celsius",
            "unit_symbol": "C",
            "min_value": -50.0,
            "max_value": 50.0,
            "default_aggregation_method": "mean",
            "health_relevance": (
                "Temperature affects heat-related health outcomes and respiratory"
                " conditions"
            ),
        },
        {
            "name": "temperature_2m_min",
            "display_name": "2m Air Temperature (Min)",
            "description": "Minimum air temperature at 2 meters above the surface",
            "category": "temperature",
            "unit": "degrees Celsius",
            "unit_symbol": "C",
            "min_value": -80.0,
            "max_value": 60.0,
            "default_aggregation_method": "min",
            "health_relevance": "Daily minima used for cold-stress and diurnal range analyses",
        },
        {
            "name": "temperature_2m_max",
            "display_name": "2m Air Temperature (Max)",
            "description": "Maximum air temperature at 2 meters above the surface",
            "category": "temperature",
            "unit": "degrees Celsius",
            "unit_symbol": "C",
            "min_value": -80.0,
            "max_value": 60.0,
            "default_aggregation_method": "max",
            "health_relevance": "Daily maxima used for heat-stress and diurnal range analyses",
        },
        {
            "name": "dewpoint_2m",
            "display_name": "2m Dew Point Temperature",
            "description": "Dew point temperature at 2 meters above the surface",
            "category": "humidity",
            "unit": "degrees Celsius",
            "unit_symbol": "C",
            "min_value": -80.0,
            "max_value": 50.0,
            "default_aggregation_method": "mean",
            "health_relevance": "Dew point informs humidity-related comfort and heat index",
        },
        {
            "name": "precipitation",
            "display_name": "Total Precipitation",
            "description": "Total precipitation amount",
            "category": "precipitation",
            "unit": "millimeters",
            "unit_symbol": "mm",
            "min_value": 0.0,
            "max_value": 500.0,
            "default_aggregation_method": "sum",
            "health_relevance": (
                "Precipitation affects waterborne diseases, vector breeding, and"
                " flooding impacts on health"
            ),
        },
        {
            "name": "surface_pressure",
            "display_name": "Surface Pressure",
            "description": "Surface atmospheric pressure",
            "category": "pressure",
            "unit": "pascal",
            "unit_symbol": "Pa",
            "min_value": 50000.0,
            "max_value": 110000.0,
            "default_aggregation_method": "mean",
            "health_relevance": "Pressure aids in altitude adjustment and weather interpretation",
        },
        {
            "name": "mean_sea_level_pressure",
            "display_name": "Mean Sea Level Pressure",
            "description": "Pressure reduced to mean sea level",
            "category": "pressure",
            "unit": "pascal",
            "unit_symbol": "Pa",
            "min_value": 50000.0,
            "max_value": 110000.0,
            "default_aggregation_method": "mean",
            "health_relevance": "MSLP supports synoptic analysis relevant to climate-health studies",
        },
        {
            "name": "relative_humidity",
            "display_name": "Relative Humidity",
            "description": "Relative humidity percentage",
            "category": "humidity",
            "unit": "percent",
            "unit_symbol": "%",
            "min_value": 0.0,
            "max_value": 100.0,
            "default_aggregation_method": "mean",
            "health_relevance": (
                "Humidity affects respiratory conditions, heat index, and pathogen"
                " survival"
            ),
        },
        {
            "name": "wind_u_10m",
            "display_name": "10m U Wind Component",
            "description": "Zonal (east-west) wind component at 10 meters",
            "category": "wind",
            "unit": "meters per second",
            "unit_symbol": "m/s",
            "min_value": -80.0,
            "max_value": 80.0,
            "default_aggregation_method": "mean",
            "health_relevance": "Wind influences dispersion of pollutants and vector movement",
        },
        {
            "name": "wind_v_10m",
            "display_name": "10m V Wind Component",
            "description": "Meridional (north-south) wind component at 10 meters",
            "category": "wind",
            "unit": "meters per second",
            "unit_symbol": "m/s",
            "min_value": -80.0,
            "max_value": 80.0,
            "default_aggregation_method": "mean",
            "health_relevance": "Wind influences dispersion of pollutants and vector movement",
        },
    ]

    DEFAULT_MAPPINGS: dict[str, list[dict[str, Any]]] = {
        "gee": [
            {
                "variable": "temperature_2m",
                "source_variable_name": "temperature_2m",
                "source_dataset": "ECMWF/ERA5/DAILY",
                "source_band": "mean_2m_air_temperature",
                "scale_factor": 1.0,
                "offset": -273.15,
                "extra_parameters": {},
            },
            {
                "variable": "temperature_2m_min",
                "source_variable_name": "temperature_2m_min",
                "source_dataset": "ECMWF/ERA5/DAILY",
                "source_band": "minimum_2m_air_temperature",
                "scale_factor": 1.0,
                "offset": -273.15,
                "extra_parameters": {},
            },
            {
                "variable": "temperature_2m_max",
                "source_variable_name": "temperature_2m_max",
                "source_dataset": "ECMWF/ERA5/DAILY",
                "source_band": "maximum_2m_air_temperature",
                "scale_factor": 1.0,
                "offset": -273.15,
                "extra_parameters": {},
            },
            {
                "variable": "dewpoint_2m",
                "source_variable_name": "dewpoint_2m_temperature",
                "source_dataset": "ECMWF/ERA5/DAILY",
                "source_band": "dewpoint_2m_temperature",
                "scale_factor": 1.0,
                "offset": -273.15,
                "extra_parameters": {},
            },
            {
                "variable": "precipitation",
                "source_variable_name": "total_precipitation",
                "source_dataset": "ECMWF/ERA5/DAILY",
                "source_band": "total_precipitation",
                "scale_factor": 1000.0,
                "offset": 0.0,
                "extra_parameters": {},
            },
            {
                "variable": "surface_pressure",
                "source_variable_name": "surface_pressure",
                "source_dataset": "ECMWF/ERA5/DAILY",
                "source_band": "surface_pressure",
                "scale_factor": 1.0,
                "offset": 0.0,
                "extra_parameters": {},
            },
            {
                "variable": "mean_sea_level_pressure",
                "source_variable_name": "mean_sea_level_pressure",
                "source_dataset": "ECMWF/ERA5/DAILY",
                "source_band": "mean_sea_level_pressure",
                "scale_factor": 1.0,
                "offset": 0.0,
                "extra_parameters": {},
            },
            {
                "variable": "wind_u_10m",
                "source_variable_name": "u_component_of_wind_10m",
                "source_dataset": "ECMWF/ERA5/DAILY",
                "source_band": "u_component_of_wind_10m",
                "scale_factor": 1.0,
                "offset": 0.0,
                "extra_parameters": {},
            },
            {
                "variable": "wind_v_10m",
                "source_variable_name": "v_component_of_wind_10m",
                "source_dataset": "ECMWF/ERA5/DAILY",
                "source_band": "v_component_of_wind_10m",
                "scale_factor": 1.0,
                "offset": 0.0,
                "extra_parameters": {},
            },
        ],
    }

    def handle(self, *args, **options):
        variables = self._ensure_core_variables()
        compiled_parameters: dict[str, dict[str, Any]] = {}

        for source_type, cfg in self.DEFAULT_SERVICE_CONFIG.items():
            data_source, compiled_info = self._sync_data_source(source_type, cfg)
            self._ensure_default_mappings(source_type, data_source, variables)
            self._apply_parameters_to_mappings(data_source, cfg.get("parameters", {}))
            compiled_parameters[source_type] = compiled_info

        settings.CLIMATE_SERVICE_PARAMETERS = compiled_parameters
        self.stdout.write(
            self.style.SUCCESS(
                "Climate service parameters loaded into "
                "settings.CLIMATE_SERVICE_PARAMETERS",
            ),
        )

    def _sync_data_source(
        self,
        source_type: str,
        cfg: dict[str, Any],
    ) -> tuple[ClimateDataSource, dict[str, Any]]:
        ds_defaults = {
            "name": cfg["name"],
            "description": cfg["description"],
            "api_endpoint": cfg.get("api_endpoint", ""),
            "requires_authentication": True,
        }

        data_source, created = ClimateDataSource.objects.get_or_create(
            source_type=source_type,
            defaults=ds_defaults,
        )

        updated_fields: list[str] = []
        if data_source.name != cfg["name"]:
            data_source.name = cfg["name"]
            updated_fields.append("name")

        if cfg.get("description") and data_source.description != cfg["description"]:
            data_source.description = cfg["description"]
            updated_fields.append("description")

        api_endpoint = cfg.get("api_endpoint")
        if api_endpoint and data_source.api_endpoint != api_endpoint:
            data_source.api_endpoint = api_endpoint
            updated_fields.append("api_endpoint")

        credential_env = cfg.get("credential_env")
        credential_value = ""
        if credential_env:
            credential_value = os.getenv(credential_env, "").strip()
            if credential_value and data_source.api_key != credential_value:
                data_source.api_key = credential_value
                updated_fields.append("api_key")

        if updated_fields:
            data_source.save(update_fields=updated_fields)
            message = (
                f"Updated {data_source.name} ({source_type}): "
                f"{', '.join(updated_fields)}"
            )
            self.stdout.write(self.style.SUCCESS(message))
        elif created:
            create_message = f"Created data source {data_source.name} ({source_type})"
            self.stdout.write(self.style.SUCCESS(create_message))
        else:
            unchanged_message = (
                f"No changes for data source {data_source.name} ({source_type})"
            )
            self.stdout.write(unchanged_message)

        compiled_info = {
            "api_endpoint": data_source.api_endpoint,
            "credential_env": credential_env,
            "credential_set": bool(credential_value),
            "parameters": cfg.get("parameters", {}) or {},
        }

        return data_source, compiled_info

    def _apply_parameters_to_mappings(
        self,
        data_source: ClimateDataSource,
        params: dict[str, Any],
    ) -> None:
        if not params:
            return

        mappings = ClimateVariableMapping.objects.filter(data_source=data_source)
        for mapping in mappings:
            merged = {**mapping.extra_parameters, **params}
            if merged != mapping.extra_parameters:
                mapping.extra_parameters = merged
                mapping.save(update_fields=["extra_parameters"])
                mapping_message = (
                    "Updated extra_parameters for mapping "
                    f"{mapping} with {params}"
                )
                self.stdout.write(mapping_message)

    def _ensure_core_variables(self) -> dict[str, ClimateVariable]:
        """Seed a minimal set of climate variables for UI selection and mapping."""
        variables: dict[str, ClimateVariable] = {}

        for variable_cfg in self.CORE_VARIABLES:
            defaults = {
                "display_name": variable_cfg["display_name"],
                "description": variable_cfg["description"],
                "category": variable_cfg["category"],
                "unit": variable_cfg["unit"],
                "unit_symbol": variable_cfg["unit_symbol"],
                "min_value": variable_cfg.get("min_value"),
                "max_value": variable_cfg.get("max_value"),
                "supports_temporal_aggregation": True,
                "supports_spatial_aggregation": True,
                "default_aggregation_method": variable_cfg[
                    "default_aggregation_method"
                ],
                "health_relevance": variable_cfg["health_relevance"],
            }

            variable, created = ClimateVariable.objects.get_or_create(
                name=variable_cfg["name"],
                defaults=defaults,
            )

            if created:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created climate variable {variable_cfg['name']}",
                    ),
                )
            variables[variable_cfg["name"]] = variable

        return variables

    def _ensure_default_mappings(
        self,
        source_type: str,
        data_source: ClimateDataSource,
        variables: dict[str, ClimateVariable],
    ) -> None:
        mappings = self.DEFAULT_MAPPINGS.get(source_type, [])

        for mapping_cfg in mappings:
            variable = variables.get(mapping_cfg["variable"])
            if not variable:
                continue

            defaults = {
                "source_variable_name": mapping_cfg["source_variable_name"],
                "source_dataset": mapping_cfg["source_dataset"],
                "source_band": mapping_cfg["source_band"],
                "scale_factor": mapping_cfg["scale_factor"],
                "offset": mapping_cfg["offset"],
                "extra_parameters": mapping_cfg.get("extra_parameters", {}),
            }

            mapping, created = ClimateVariableMapping.objects.get_or_create(
                variable=variable,
                data_source=data_source,
                defaults=defaults,
            )

            if created:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created mapping for {variable.name} -> {data_source.name}",
                    ),
                )
                continue

            updated_fields: list[str] = []
            for field, value in defaults.items():
                current_value = getattr(mapping, field)
                if current_value != value:
                    setattr(mapping, field, value)
                    updated_fields.append(field)

            if updated_fields:
                mapping.save(update_fields=updated_fields)
                self.stdout.write(
                    f"Updated mapping for {variable.name} in {data_source.name}: "
                    f"{', '.join(updated_fields)}",
                )
