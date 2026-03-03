"""
Climate data services for fetching and processing climate data from various sources.
"""
import logging
import json
from typing import Dict, List, Optional, Any, Tuple
import numpy as np
from datetime import datetime, timedelta
from django.db import models, transaction
from django.db.models import Min, Max
from django.utils import timezone
from core.models import Location, TimeDimension, Attribute, Observation
from .models import (
    ClimateDataSource,
    ClimateVariable,
    ClimateVariableMapping,
    ClimateDataRequest,
    ClimateDataCache,
)

logger = logging.getLogger(__name__)


class BaseClimateDataService:
    """Base class for climate data services."""

    def __init__(self, data_source: ClimateDataSource):
        self.data_source = data_source
        self.api_key = data_source.api_key
        self.api_endpoint = data_source.api_endpoint
        self.logger = logging.getLogger(self.__class__.__name__)
        # Safety cap for APIs that page/limit per request
        self.max_images_per_call = 4950  # keep well under EE 5000 element limit
    
    def fetch_data(
        self,
        variable: ClimateVariable,
        location: Location,
        start_date: datetime,
        end_date: datetime,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Fetch climate data for a specific variable and location.
        Must be implemented by subclasses.
        """
        raise NotImplementedError("Subclasses must implement fetch_data method")
    
    def validate_location(self, location: Location) -> bool:
        """Validate that location has required coordinates."""
        return location.latitude is not None and location.longitude is not None
    
    def validate_date_range(self, start_date: datetime, end_date: datetime) -> bool:
        """Validate date range against data source availability."""
        if self.data_source.data_start_date and start_date.date() < self.data_source.data_start_date:
            return False
        if self.data_source.data_end_date and end_date.date() > self.data_source.data_end_date:
            return False
        return start_date <= end_date


class EarthEngineDataService(BaseClimateDataService):
    """
    Google Earth Engine data service for satellite-based climate data.

    Requires valid credentials and the `earthengine-api` package. Initialization errors
    are surfaced so misconfiguration is visible during development and deployment.
    """

    def __init__(self, data_source: ClimateDataSource):
        super().__init__(data_source)
        self.ee = None
        self._initialize_earth_engine()

    def _initialize_earth_engine(self) -> None:
        """Initialize the Earth Engine client with service account or user auth."""
        try:
            import os
            import json
            import ee
            from google.oauth2 import service_account

            credentials_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
#not sure if this is correct pathing for docker deployment
            if credentials_path:
                resolved_path = os.path.abspath(credentials_path)
                if os.path.exists(resolved_path):
                    self.logger.info(
                        "Initializing Google Earth Engine with service account credentials"
                    )
                    credentials = service_account.Credentials.from_service_account_file(
                        resolved_path,
                        scopes=['https://www.googleapis.com/auth/earthengine']
                    )
                    with open(resolved_path) as f:
                        creds_data = json.load(f)
                        project_id = creds_data.get('project_id')

                    ee.Initialize(credentials=credentials, project=project_id)
                    self.logger.info(
                        f"Google Earth Engine initialized successfully with project: {project_id}"
                    )
                else:
                    self.logger.warning(
                        "GOOGLE_APPLICATION_CREDENTIALS is set but file was not found at %s; attempting interactive auth",
                        resolved_path,
                    )
                    ee.Authenticate()  # may prompt if running interactively
                    ee.Initialize()
            else:
                self.logger.info("Initializing Google Earth Engine with default credentials")
                ee.Initialize()

            self.ee = ee

        except ImportError as exc:
            raise RuntimeError(
                "earthengine-api not installed; install it and configure credentials to fetch climate data"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Failed to initialize Earth Engine: {exc}") from exc
    
    def fetch_data(
        self,
        variable: ClimateVariable,
        location: Location,
        start_date: datetime,
        end_date: datetime,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """Fetch data from Google Earth Engine."""
        if not self.validate_location(location):
            raise ValueError(f"Invalid location coordinates: {location}")

        if not self.validate_date_range(start_date, end_date):
            raise ValueError(f"Invalid date range for data source")

        # Get variable mapping for Earth Engine
        try:
            mapping = ClimateVariableMapping.objects.get(
                variable=variable,
                data_source=self.data_source
            )
        except ClimateVariableMapping.DoesNotExist:
            raise ValueError(f"Variable {variable} not available in {self.data_source}")

        return self._fetch_gee_data(variable, location, start_date, end_date, mapping)

    def _fetch_gee_data(
        self,
        variable: ClimateVariable,
        location: Location,
        start_date: datetime,
        end_date: datetime,
        mapping: ClimateVariableMapping
    ) -> List[Dict[str, Any]]:
        """
        Fetch data from Google Earth Engine API.

        Example for ERA5 temperature:
        - Dataset: ECMWF/ERA5/DAILY
        - Band: mean_2m_air_temperature
        - Point extraction at location coordinates
        """
        if not self.ee:
            raise RuntimeError("Earth Engine not initialized")

        try:
            # Define point geometry
            point = self.ee.Geometry.Point([location.longitude, location.latitude])

            # Load image collection; GEE filterDate end is exclusive, so add one day
            end_exclusive = end_date + timedelta(days=1)
            collection = (
                self.ee.ImageCollection(mapping.source_dataset)
                .filterDate(
                    start_date.strftime('%Y-%m-%d'),
                    end_exclusive.strftime('%Y-%m-%d'),
                )
                .filterBounds(point)
                # Apply a hard cap before mapping to avoid EE 5000-element aborts
                .limit(self.max_images_per_call)
            )

            # Extract time series at point
            def extract_value(image):
                """Extract value from image at point location."""
                # Reduce region to get value
                value_dict = image.select(mapping.source_band).reduceRegion(
                    reducer=self.ee.Reducer.first(),
                    geometry=point,
                    scale=self.data_source.spatial_resolution_m or 1000,
                    maxPixels=1e9
                )

                # Get value for the band
                value = value_dict.get(mapping.source_band)

                # Get image date
                date = image.date().format('YYYY-MM-dd')

                return self.ee.Feature(None, {
                    'date': date,
                    'value': value,
                    'band': mapping.source_band
                })

            # Map over collection
            features = collection.map(extract_value).getInfo()

            # Process results
            data = []
            for feature in features['features']:
                props = feature['properties']
                raw_value = props.get('value')

                if raw_value is not None:
                    # Apply scaling and offset
                    value = float(raw_value) * mapping.scale_factor + mapping.offset

                    data.append({
                        'date': datetime.strptime(props['date'], '%Y-%m-%d').date(),
                        'value': value,
                        'quality_flag': 'good',
                        'source': 'Earth Engine',
                    })
                else:
                    self.logger.warning(f"No data for {props.get('date')} - likely cloud cover or missing data")

            self.logger.info(f"Fetched {len(data)} values from GEE for {variable.name}")
            return data

        except Exception as e:
            self.logger.error(f"Error fetching GEE data: {e}")
            raise RuntimeError(
                f"Failed to fetch band '{mapping.source_band}' from dataset '{mapping.source_dataset}': {e}"
            ) from e

class CopernicusDataService(BaseClimateDataService):
    """
    Copernicus Climate Data Store (CDS) service for ERA5 reanalysis data.

    Requires a configured CDS API client. Initialization errors are raised so missing
    dependencies or credentials are visible immediately.
    """

    def __init__(self, data_source: ClimateDataSource):
        super().__init__(data_source)
        self.cds_client = None
        self._initialize_cds_client()

    def _initialize_cds_client(self) -> None:
        """Initialize the Copernicus CDS API client."""
        try:
            import cdsapi

            self.cds_client = cdsapi.Client()
            self.logger.info("Copernicus CDS client initialized successfully")
        except ImportError as exc:
            raise RuntimeError(
                "cdsapi not installed; install it and configure credentials to fetch CDS data"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Failed to initialize CDS client: {exc}") from exc

    def fetch_data(
        self,
        variable: ClimateVariable,
        location: Location,
        start_date: datetime,
        end_date: datetime,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """Fetch data from Copernicus Climate Data Store."""
        if not self.validate_location(location):
            raise ValueError(f"Invalid location coordinates: {location}")

        if not self.validate_date_range(start_date, end_date):
            raise ValueError(f"Invalid date range for data source")

        # Get variable mapping
        try:
            mapping = ClimateVariableMapping.objects.get(
                variable=variable,
                data_source=self.data_source
            )
        except ClimateVariableMapping.DoesNotExist:
            raise ValueError(f"Variable {variable} not available in {self.data_source}")

        return self._fetch_cds_data(variable, location, start_date, end_date, mapping)

    def _fetch_cds_data(
        self,
        variable: ClimateVariable,
        location: Location,
        start_date: datetime,
        end_date: datetime,
        mapping: ClimateVariableMapping
    ) -> List[Dict[str, Any]]:
        """
        Fetch data from Copernicus CDS API.

        Example request for ERA5 2m temperature:
        - Dataset: reanalysis-era5-single-levels
        - Variable: 2m_temperature
        - Format: NetCDF
        - Area: [north, west, south, east] in degrees
        """
        if not self.cds_client:
            raise RuntimeError("CDS client not initialized")

        try:
            import tempfile
            import xarray as xr
            from pathlib import Path

            # Define bounding box (with small buffer for point extraction)
            buffer = 0.25  # degrees (~27.5 km)
            area = [
                location.latitude + buffer,  # North
                location.longitude - buffer,  # West
                location.latitude - buffer,  # South
                location.longitude + buffer,  # East
            ]

            # Build date list
            years = list(range(start_date.year, end_date.year + 1))
            months = [f"{m:02d}" for m in range(1, 13)]
            days = [f"{d:02d}" for d in range(1, 32)]

            # CDS request parameters
            request_params = {
                'product_type': 'reanalysis',
                'format': 'netcdf',
                'variable': mapping.source_variable_name,
                'year': [str(y) for y in years],
                'month': months,
                'day': days,
                'time': '12:00',  # Midday for daily data
                'area': area,
            }

            # Add extra parameters from mapping
            if mapping.extra_parameters:
                request_params.update(mapping.extra_parameters)

            # Download data to temporary file
            with tempfile.NamedTemporaryFile(suffix='.nc', delete=False) as tmp_file:
                tmp_path = Path(tmp_file.name)

            try:
                self.logger.info(f"Requesting data from CDS for {variable.name}")
                self.cds_client.retrieve(
                    mapping.source_dataset,
                    request_params,
                    str(tmp_path)
                )

                # Read NetCDF file with xarray
                ds = xr.open_dataset(tmp_path)

                # Extract variable at nearest point
                ds_point = ds.sel(
                    latitude=location.latitude,
                    longitude=location.longitude,
                    method='nearest'
                )

                # Convert to list of daily values
                data = []
                for time_val in ds_point.time.values:
                    date_val = datetime.fromisoformat(str(time_val)[:10])

                    # Filter to requested date range
                    if start_date.date() <= date_val.date() <= end_date.date():
                        # Get value and apply scaling/offset
                        raw_value = float(ds_point[mapping.source_band].sel(time=time_val).values)
                        value = raw_value * mapping.scale_factor + mapping.offset

                        data.append({
                            'date': date_val.date(),
                            'value': value,
                            'quality_flag': 'good',
                            'source': 'Copernicus CDS',
                        })

                ds.close()
                self.logger.info(f"Fetched {len(data)} values from CDS for {variable.name}")
                return data

            finally:
                # Clean up temporary file
                if tmp_path.exists():
                    tmp_path.unlink()

        except Exception as e:
            self.logger.error(f"Error fetching CDS data: {e}")
            raise

class ClimateDataProcessor:
    """
    Processes and harmonises climate data for integration with health data.
    """
    
    def __init__(self, request: ClimateDataRequest):
        self.request = request
        self.logger = logging.getLogger(self.__class__.__name__)
        # Limit per-call date span to avoid huge API payloads
        self.max_days_per_batch = 10
        self.max_batch_span_days = 14
        self._retrieval_errors: List[Dict[str, Any]] = []
    
    def process_request(self) -> Dict[str, Any]:
        """
        Process a climate data request end-to-end.
        """
        try:
            self.request.status = 'processing'
            self.request.started_at = timezone.now()
            self.request.save()
            
            # Optionally remove previously fetched climate observations for this window
            if self.request.reset_existing:
                self._delete_existing_climate_observations()

            # Get data service for the source
            service = self._get_data_service()
            
            # Process each location
            total_observations = 0
            locations = self.request.locations.all()
            variables_count = self.request.variables.count()
            
            # Estimate total work units (Location * Variable * Days)
            date_span = (self.request.end_date - self.request.start_date).days + 1
            if date_span < 1: date_span = 1
            
            self.request.total_locations = locations.count()
            self.request.total_estimated_units = self.request.total_locations * variables_count * date_span
            self.request.processed_units = 0
            self.request.save()
            
            for idx, location in enumerate(locations):
                # Allow cooperative cancellation mid-run
                self.request.refresh_from_db(fields=['status'])
                if self.request.status == 'cancelled':
                    self.logger.info("Request %s cancelled mid-run; stopping", self.request.id)
                    self.request.completed_at = timezone.now()
                    self.request.save(update_fields=['completed_at'])
                    return {
                        'status': 'cancelled',
                        'total_observations': total_observations,
                        'duration_seconds': self.request.duration.total_seconds() if self.request.duration else None,
                    }

                obs_count = self._process_location(service, location, variables_count, date_span)
                total_observations += obs_count
                
                # Update progress
                self.request.processed_locations = idx + 1
                self.request.total_observations = total_observations
                # Ensure we don't undershoot due to estimation rounding or gaps if _process_location didn't purely add up
                # (handled inside _process_location now, but safety sync here could be good)
                self.request.save()
            
            if total_observations == 0:
                self.request.status = 'failed'
                self.request.error_message = (
                    'Failed to retrieve report: no climate data returned from the API.'
                )
                self.request.completed_at = timezone.now()
                self.request.save(update_fields=['status', 'error_message', 'completed_at'])

                return {
                    'status': 'failed',
                    'error': self.request.error_message,
                    'total_observations': total_observations,
                }

            # Mark as completed
            self.request.status = 'completed'
            self.request.completed_at = timezone.now()

            error_report = self._build_error_report()
            if error_report:
                self.request.error_message = error_report
                self.request.save(update_fields=['status', 'completed_at', 'error_message'])
            else:
                self.request.save(update_fields=['status', 'completed_at'])

            return {
                'status': 'success',
                'total_observations': total_observations,
                'duration_seconds': self.request.duration.total_seconds() if self.request.duration else None,
                'errors': self._retrieval_errors if self._retrieval_errors else None,
            }
            
        except Exception as e:
            self.logger.error(f"Error processing climate request: {e}")
            self.request.status = 'failed'
            self.request.error_message = str(e)
            self.request.completed_at = timezone.now()
            self.request.save()
            
            return {
                'status': 'failed',
                'error': str(e),
            }
    
    def _get_data_service(self) -> BaseClimateDataService:
        """
        Get appropriate data service based on source type.

        Service mapping:
        - 'gee': Google Earth Engine (GEE)
        - 'era5': Copernicus CDS (ERA5 reanalysis)
        - 'chirps': Currently via GEE
        - 'modis': Currently via GEE
        - 'worldclim': Could be added later
        """
        source_type = self.request.data_source.source_type

        if source_type == 'gee':
            return EarthEngineDataService(self.request.data_source)
        elif source_type in ['era5', 'worldclim']:
            # ERA5 and WorldClim available via Copernicus CDS
            return CopernicusDataService(self.request.data_source)
        elif source_type in ['chirps', 'modis']:
            # CHIRPS and MODIS available via Google Earth Engine
            return EarthEngineDataService(self.request.data_source)
        else:
            raise NotImplementedError(
                f"Service for {source_type} not implemented. "
                f"Available: gee, era5, chirps, modis"
            )

    def _delete_existing_climate_observations(self) -> int:
        """Remove existing climate observations and cached entries for this request window."""
        locations = list(self.request.locations.all())
        variables = list(self.request.variables.all())

        if not locations or not variables:
            return 0

        attributes = [self._get_or_create_climate_attribute(v) for v in variables]

        deleted_obs, _ = Observation.objects.filter(
            location__in=locations,
            attribute__in=attributes,
            time__timestamp__date__gte=self.request.start_date,
            time__timestamp__date__lte=self.request.end_date,
        ).delete()

        ClimateDataCache.objects.filter(
            data_source=self.request.data_source,
            variable__in=variables,
            location__in=locations,
            date__gte=self.request.start_date,
            date__lte=self.request.end_date,
        ).delete()

        self.logger.info(
            "Deleted %s existing climate observations and cleared cache entries before processing",
            deleted_obs,
        )
        return deleted_obs
    
    def _process_location(
        self,
        service: BaseClimateDataService,
        location: Location,
        variables_count: int,
        date_span: int
    ) -> int:
        """Process climate data for a single location."""
        observations_created = 0

        target_dates = self._compute_location_dates(location)
        if not target_dates:
            self.logger.info("No observation dates found for location %s; skipping", location)
            # Count this location as fully processed (all vars, all days skipped)
            self._increment_processed_units(variables_count * date_span)
            return 0

        # Count skipped days (days estimated but not actually processed)
        actual_days_to_process = len(target_dates)
        skipped_days = max(0, date_span - actual_days_to_process)
        if skipped_days > 0:
            self._increment_processed_units(skipped_days * variables_count)

        target_date_set = set(target_dates)

        for variable in self.request.variables.all():
            attribute = self._get_or_create_climate_attribute(variable)

            existing_dates = set(
                Observation.objects.filter(
                    location=location,
                    attribute=attribute,
                    time__timestamp__date__in=target_date_set,
                ).values_list('time__timestamp__date', flat=True)
            )

            if existing_dates:
                self._increment_processed_units(len(existing_dates))

            missing_dates = [d for d in target_dates if d not in existing_dates]
            if not missing_dates:
                continue

            # Chunk dates to respect API limits and avoid wide spans
            date_batches = self._chunk_dates(missing_dates)

            for batch in date_batches:
                batch_start = min(batch)
                batch_end = max(batch)

                # Check cache for batch window
                cached_data = self._get_cached_data(variable, location, batch_start, batch_end)

                target_set = set(batch)
                combined_by_date: Dict[datetime.date, Dict[str, Any]] = {}

                if cached_data:
                    for item in cached_data:
                        if item.get("date") in target_set:
                            combined_by_date[item["date"]] = item

                missing_after_cache = target_set - set(combined_by_date.keys())
                fetch_error: Optional[str] = None

                if missing_after_cache:
                    fetched_data, fetch_error = self._attempt_fetch_with_retry(
                        service=service,
                        variable=variable,
                        location=location,
                        batch_start=batch_start,
                        batch_end=batch_end,
                    )

                    if fetch_error:
                        self._record_retrieval_error(
                            location=location,
                            variable=variable,
                            dates=sorted(missing_after_cache),
                            reason=f"Fetch failed: {fetch_error}",
                        )
                    else:
                        self._cache_data(variable, location, fetched_data)
                        for item in fetched_data:
                            if item.get("date") in target_set:
                                combined_by_date[item["date"]] = item

                data_to_process = list(combined_by_date.values())

                missing_after_retry = target_set - set(combined_by_date.keys())
                if missing_after_retry:
                    reason = (
                        f"Missing data after retry" if not fetch_error else f"Missing data; {fetch_error}"
                    )
                    self._record_retrieval_error(
                        location=location,
                        variable=variable,
                        dates=sorted(missing_after_retry),
                        reason=reason,
                    )

                # Apply temporal aggregation if needed
                if self.request.temporal_aggregation != 'none':
                    data_to_process = self._aggregate_temporal(
                        data_to_process,
                        self.request.temporal_aggregation
                    )

                observations_created += self._create_observations(
                    variable,
                    location,
                    data_to_process,
                    attribute,
                )

                # Update progress for processed days in this batch
                self._increment_processed_units(len(batch))
        
        return observations_created

    def _chunk_dates(self, dates: List[datetime.date]) -> List[List[datetime.date]]:
        """Group dates into batches by size and contiguous span to keep EE collections small."""
        if not dates:
            return []

        batches = []
        current = []
        batch_start = None

        for d in dates:
            if not current:
                current = [d]
                batch_start = d
                continue

            span_days = (d - batch_start).days
            if len(current) >= self.max_days_per_batch or span_days >= self.max_batch_span_days:
                batches.append(current)
                current = [d]
                batch_start = d
            else:
                current.append(d)

        if current:
            batches.append(current)
        return batches

    def _attempt_fetch_with_retry(
        self,
        service: BaseClimateDataService,
        variable: ClimateVariable,
        location: Location,
        batch_start: datetime.date,
        batch_end: datetime.date,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Fetch data with a single retry if the response is empty."""
        try:
            data = service.fetch_data(
                variable=variable,
                location=location,
                start_date=datetime.combine(batch_start, datetime.min.time()),
                end_date=datetime.combine(batch_end, datetime.min.time()),
            )
        except Exception as exc:
            self.logger.error(
                "Fetch failed for %s (%s) from %s to %s: %s",
                location,
                variable.name,
                batch_start,
                batch_end,
                exc,
            )
            return [], str(exc)

        if data:
            return data, None

        self.logger.warning(
            "Empty response for %s (%s) from %s to %s; retrying once",
            location,
            variable.name,
            batch_start,
            batch_end,
        )

        try:
            retry_data = service.fetch_data(
                variable=variable,
                location=location,
                start_date=datetime.combine(batch_start, datetime.min.time()),
                end_date=datetime.combine(batch_end, datetime.min.time()),
            )
        except Exception as exc:
            self.logger.error(
                "Retry fetch failed for %s (%s) from %s to %s: %s",
                location,
                variable.name,
                batch_start,
                batch_end,
                exc,
            )
            return [], str(exc)

        if retry_data:
            return retry_data, None

        return [], "Empty response after retry"

    def _compute_location_dates(self, location: Location) -> Optional[List[datetime.date]]:
        """Determine exact dates to fetch for a location, respecting lags and gaps.

        - Start from actual observation dates for this study/location.
        - For each observation date, include lag back to configured lag_value/lag_unit.
        - Exclude dates outside data source availability and outside the configured request window.
        - Do not query dates between earliest/latest if there was no observation on that date.
        """
        obs_dates = Observation.objects.filter(
            location=location,
            attribute__study=self.request.study,
            time__timestamp__date__gte=self.request.start_date,
            time__timestamp__date__lte=self.request.end_date,
        ).values_list('time__timestamp__date', flat=True)

        if not obs_dates:
            return None

        # Determine lag in days from configuration
        cfg = self.request.configuration or {}
        lag_value = cfg.get('lag_value') or 0
        lag_unit = cfg.get('lag_unit') or 'days'
        unit_days = {
            'days': 1,
            'weeks': 7,
            'months': 30,
            'years': 365,
        }.get(lag_unit, 1)
        lag_span = max(int(lag_value) * unit_days, 0)

        ds = self.request.data_source
        ds_start = getattr(ds, 'data_start_date', None)
        ds_end = getattr(ds, 'data_end_date', None)

        date_set = set()
        for obs_date in obs_dates:
            for offset in range(lag_span + 1):
                candidate = obs_date - timedelta(days=offset)
                if ds_start and candidate < ds_start:
                    continue
                if ds_end and candidate > ds_end:
                    continue
                date_set.add(candidate)

        if not date_set:
            return None

        return sorted(date_set)

    def _record_retrieval_error(
        self,
        location: Location,
        variable: ClimateVariable,
        dates: List[datetime.date],
        reason: str,
    ) -> None:
        """Track retrieval errors for reporting."""
        if not dates:
            return

        entry = {
            "location_id": location.id,
            "location_name": getattr(location, "name", str(location)),
            "latitude": location.latitude,
            "longitude": location.longitude,
            "variable": variable.name,
            "dates": [d.isoformat() for d in dates],
            "reason": reason,
        }
        self._retrieval_errors.append(entry)

    def _build_error_report(self) -> Optional[str]:
        """Create a compact error report for missing or failed retrievals."""
        if not self._retrieval_errors:
            return None

        report = {
            "summary": {
                "total_error_groups": len(self._retrieval_errors),
                "request_id": self.request.id,
                "data_source": getattr(self.request.data_source, "name", None),
            },
            "errors": self._retrieval_errors,
        }
        return json.dumps(report, indent=2)
    
    def _get_cached_data(
        self,
        variable: ClimateVariable,
        location: Location,
        start_date,
        end_date,
    ) -> Optional[List[Dict[str, Any]]]:
        """Retrieve data from cache if available for the per-location window."""
        cached_entries = ClimateDataCache.objects.filter(
            data_source=self.request.data_source,
            variable=variable,
            location=location,
            date__gte=start_date,
            date__lte=end_date,
            expires_at__gt=timezone.now()
        ).order_by('date')
        
        if cached_entries.exists():
            # Update hit counts
            from django.db.models import F
            cached_entries.update(hit_count=F('hit_count') + 1)
            
            return [
                {
                    'date': entry.date,
                    'value': entry.value,
                    'quality_flag': entry.quality_flag,
                    'source': 'cache',
                }
                for entry in cached_entries
            ]
        
        return None
    
    def _cache_data(
        self,
        variable: ClimateVariable,
        location: Location,
        data: List[Dict[str, Any]]
    ) -> None:
        """Cache fetched data for future use."""
        cache_entries = []
        for item in data:
            if item.get('source') != 'cache':  # Don't re-cache cached data
                cache_entry = ClimateDataCache(
                    data_source=self.request.data_source,
                    variable=variable,
                    location=location,
                    date=item['date'],
                    value=item['value'],
                    quality_flag=item.get('quality_flag', ''),
                )
                # Set expiration to 30 days from now (will be set in save() method)
                cache_entry.expires_at = timezone.now() + timezone.timedelta(days=30)
                cache_entries.append(cache_entry)
        
        if cache_entries:
            ClimateDataCache.objects.bulk_create(
                cache_entries,
                ignore_conflicts=True
            )
    
    def _aggregate_temporal(
        self,
        data: List[Dict[str, Any]],
        aggregation: str
    ) -> List[Dict[str, Any]]:
        """
        Aggregate data temporally based on specified method.
        """
        if not data:
            return []
        
        # Group data by aggregation period
        grouped_data = {}
        
        for item in data:
            date = item['date']
            
            if aggregation == 'daily':
                key = date
            elif aggregation == 'weekly':
                key = date - timedelta(days=date.weekday())
            elif aggregation == 'monthly':
                key = date.replace(day=1)
            elif aggregation == 'annual':
                key = date.replace(month=1, day=1)
            else:
                key = date
            
            if key not in grouped_data:
                grouped_data[key] = []
            grouped_data[key].append(item['value'])
        
        # Aggregate values
        aggregated = []
        for date_key, values in grouped_data.items():
            aggregated.append({
                'date': date_key,
                'value': np.mean(values),  # Use mean as default
                'quality_flag': 'aggregated',
                'source': 'aggregated',
            })
        
        return aggregated

    def _get_or_create_climate_attribute(self, variable: ClimateVariable) -> Attribute:
        """Return the Attribute used for this climate variable (shared/global)."""
        # Climate data is uniform and shared across studies (no study-specific suffix, no study fk)
        variable_name = f"climate_{variable.name}"

        # Try to find existing global attribute
        # We use source_type='target' because climate data is pre-standardized (clean)
        attribute = Attribute.objects.filter(
            variable_name=variable_name,
            source_type='target', 
            study__isnull=True  # Ensure it's a global attribute
        ).first()

        if attribute is None:
            attribute = Attribute.objects.create(
                variable_name=variable_name,
                source_type='target',
                display_name=variable.display_name,
                description=variable.description,
                unit=variable.unit,
                variable_type='float',
                category='climate',
                study=None,  # Explicitly None for global sharing
            )

        # Ensure the attribute is linked to the requesting study so it appears in exports
        if self.request.study:
            self.request.study.variables.add(attribute)

        return attribute

    def _increment_processed_units(self, count: int) -> None:
        """Advance processed_units without exceeding the estimate."""
        if count <= 0:
            return

        self.request.processed_units = min(
            self.request.processed_units + count,
            self.request.total_estimated_units,
        )
        self.request.save(update_fields=['processed_units'])
    
    def _create_observations(
        self,
        variable: ClimateVariable,
        location: Location,
        data: List[Dict[str, Any]],
        attribute: Optional[Attribute] = None,
    ) -> int:
        """Create observation records from processed data."""
        attribute = attribute or self._get_or_create_climate_attribute(variable)

        observations_created = 0
        
        with transaction.atomic():
            for item in data:
                # Get or create time dimension
                time_dim, _ = TimeDimension.objects.get_or_create(
                    timestamp=timezone.make_aware(
                        datetime.combine(item['date'], datetime.min.time())
                    )
                )
                
                # Create observation
                observation, created = Observation.objects.update_or_create(
                    location=location,
                    attribute=attribute,
                    time=time_dim,
                    defaults={
                        'float_value': item['value'],
                    }
                )
                
                if created:
                    observations_created += 1
        
        return observations_created


class SpatioTemporalMatcher:
    """
    Matches study data locations and time periods with climate data grids.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
    
    def match_study_locations(
        self,
        study_locations: List[Location],
        buffer_km: float = 0
    ) -> List[Tuple[Location, Dict[str, Any]]]:
        """
        Match study locations to climate data grid points.
        Returns list of (location, metadata) tuples.
        """
        matched = []
        
        for location in study_locations:
            if not (location.latitude and location.longitude):
                self.logger.warning(f"Skipping location {location} - missing coordinates")
                continue
            
            metadata = {
                'original_location': location,
                'buffer_km': buffer_km,
            }
            
            if buffer_km > 0:
                # Calculate buffer bounds
                # Approximate: 1 degree latitude = 111 km
                lat_buffer = buffer_km / 111
                # Longitude buffer varies by latitude
                lon_buffer = buffer_km / (111 * np.cos(np.radians(location.latitude)))
                
                metadata['bounds'] = {
                    'north': location.latitude + lat_buffer,
                    'south': location.latitude - lat_buffer,
                    'east': location.longitude + lon_buffer,
                    'west': location.longitude - lon_buffer,
                }
            
            matched.append((location, metadata))
        
        return matched
    
    def align_time_periods(
        self,
        study_start: datetime,
        study_end: datetime,
        climate_resolution_days: int = 1
    ) -> List[datetime]:
        """
        Align study time period with climate data temporal resolution.
        Returns list of dates to fetch climate data for.
        """
        dates = []
        current = study_start
        
        while current <= study_end:
            dates.append(current)
            current += timedelta(days=climate_resolution_days)
        
        return dates