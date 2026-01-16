"""
Climate data services for fetching and processing climate data from various sources.
"""
import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from django.db import transaction
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
            raise

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
    
    def process_request(self) -> Dict[str, Any]:
        """
        Process a climate data request end-to-end.
        """
        try:
            self.request.status = 'processing'
            self.request.started_at = timezone.now()
            self.request.save()
            
            # Get data service for the source
            service = self._get_data_service()
            
            # Process each location
            total_observations = 0
            locations = self.request.locations.all()
            self.request.total_locations = locations.count()
            self.request.save()
            
            for idx, location in enumerate(locations):
                obs_count = self._process_location(service, location)
                total_observations += obs_count
                
                # Update progress
                self.request.processed_locations = idx + 1
                self.request.total_observations = total_observations
                self.request.save()
            
            # Mark as completed
            self.request.status = 'completed'
            self.request.completed_at = timezone.now()
            self.request.save()
            
            return {
                'status': 'success',
                'total_observations': total_observations,
                'duration': self.request.duration,
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
    
    def _process_location(
        self,
        service: BaseClimateDataService,
        location: Location
    ) -> int:
        """Process climate data for a single location."""
        observations_created = 0
        
        for variable in self.request.variables.all():
            # Check cache first
            cached_data = self._get_cached_data(variable, location)
            
            if cached_data:
                data_to_process = cached_data
            else:
                # Fetch new data
                data_to_process = service.fetch_data(
                    variable=variable,
                    location=location,
                    start_date=datetime.combine(self.request.start_date, datetime.min.time()),
                    end_date=datetime.combine(self.request.end_date, datetime.min.time()),
                )
                
                # Cache the data
                self._cache_data(variable, location, data_to_process)
            
            # Apply temporal aggregation if needed
            if self.request.temporal_aggregation != 'none':
                data_to_process = self._aggregate_temporal(
                    data_to_process,
                    self.request.temporal_aggregation
                )
            
            # Create observations
            observations_created += self._create_observations(
                variable,
                location,
                data_to_process
            )
        
        return observations_created
    
    def _get_cached_data(
        self,
        variable: ClimateVariable,
        location: Location
    ) -> Optional[List[Dict[str, Any]]]:
        """Retrieve data from cache if available."""
        cached_entries = ClimateDataCache.objects.filter(
            data_source=self.request.data_source,
            variable=variable,
            location=location,
            date__gte=self.request.start_date,
            date__lte=self.request.end_date,
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
    
    def _create_observations(
        self,
        variable: ClimateVariable,
        location: Location,
        data: List[Dict[str, Any]]
    ) -> int:
        """Create observation records from processed data."""
        # Get or create climate attribute
        attribute, created = Attribute.objects.get_or_create(
            variable_name=f"climate_{variable.name}",
            defaults={
                'display_name': variable.display_name,
                'description': variable.description,
                'unit': variable.unit,
                'variable_type': 'float',
                'category': 'climate',
                'source_type': 'source',
            }
        )
        
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