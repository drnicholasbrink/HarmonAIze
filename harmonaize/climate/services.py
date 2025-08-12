"""
Climate data services for fetching and processing climate data from various sources.
"""
import logging
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
import numpy as np
from django.utils import timezone
from django.db import transaction, models
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


class GEEDataService(BaseClimateDataService):
    """
    Google Earth Engine data service for satellite-based climate data.
    Note: This is a simplified implementation. Real GEE integration would require
    the earthengine-api package and proper authentication.
    """
    
    def __init__(self, data_source: ClimateDataSource):
        super().__init__(data_source)
        # In production, initialize GEE here:
        # import ee
        # ee.Initialize(credentials)
    
    def fetch_data(
        self,
        variable: ClimateVariable,
        location: Location,
        start_date: datetime,
        end_date: datetime,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Fetch data from Google Earth Engine.
        This is a mock implementation for the MVP.
        """
        if not self.validate_location(location):
            raise ValueError(f"Invalid location coordinates: {location}")
        
        if not self.validate_date_range(start_date, end_date):
            raise ValueError(f"Invalid date range for data source")
        
        # Get variable mapping for GEE
        try:
            mapping = ClimateVariableMapping.objects.get(
                variable=variable,
                data_source=self.data_source
            )
        except ClimateVariableMapping.DoesNotExist:
            raise ValueError(f"Variable {variable} not available in {self.data_source}")
        
        # Mock data generation for MVP
        # In production, this would use the GEE Python API
        data = []
        current_date = start_date
        while current_date <= end_date:
            # Simulate fetching data from GEE
            value = self._simulate_climate_value(variable, location, current_date)
            
            # Apply scaling and offset from mapping
            value = value * mapping.scale_factor + mapping.offset
            
            data.append({
                'date': current_date.date(),
                'value': value,
                'quality_flag': 'good',
                'source': 'GEE',
            })
            
            current_date += timedelta(days=1)
        
        return data
    
    def _simulate_climate_value(
        self,
        variable: ClimateVariable,
        location: Location,
        date: datetime
    ) -> float:
        """
        Simulate climate values for MVP demonstration.
        In production, this would be replaced with actual GEE data retrieval.
        """
        # Simple simulation based on variable type and location
        base_value = 0
        
        if variable.category == 'temperature':
            # Temperature varies by latitude and season
            base_value = 20 - abs(location.latitude) / 3
            seasonal_variation = 10 * np.sin((date.timetuple().tm_yday / 365) * 2 * np.pi)
            base_value += seasonal_variation
        elif variable.category == 'precipitation':
            # Precipitation with seasonal pattern
            base_value = 50 + 30 * np.sin((date.timetuple().tm_yday / 365) * 2 * np.pi + np.pi/2)
            base_value = max(0, base_value + np.random.normal(0, 10))
        elif variable.category == 'vegetation':
            # NDVI values between 0 and 1
            base_value = 0.5 + 0.3 * np.sin((date.timetuple().tm_yday / 365) * 2 * np.pi)
            base_value = max(0, min(1, base_value))
        else:
            # Generic climate variable
            base_value = np.random.uniform(
                variable.min_value or 0,
                variable.max_value or 100
            )
        
        return base_value


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
        """Get appropriate data service based on source type."""
        if self.request.data_source.source_type == 'gee':
            return GEEDataService(self.request.data_source)
        else:
            # Add other services as needed
            raise NotImplementedError(
                f"Service for {self.request.data_source.source_type} not implemented"
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