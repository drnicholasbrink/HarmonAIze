"""
Tests for the climate module.
"""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework import status

from core.models import Project, Study, Location, TimeDimension, Attribute, Observation
from .models import (
    ClimateDataSource,
    ClimateVariable,
    ClimateVariableMapping,
    ClimateDataRequest,
    ClimateDataCache,
)
from .services import ClimateDataProcessor, EarthEngineDataService, SpatioTemporalMatcher

User = get_user_model()


class ClimateModelsTestCase(TestCase):
    """Test climate data models."""
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
        
        self.project = Project.objects.create(
            name='Test Project',
            description='Test project for climate data',
            created_by=self.user
        )
        
        self.study = Study.objects.create(
            name='Test Study',
            description='Test study',
            created_by=self.user,
            project=self.project,
            study_type='cohort',
            needs_climate_linkage=True
        )
        
        self.location = Location.objects.create(
            name='Test Location',
            latitude=-26.2041,
            longitude=28.0473
        )
    
    def test_climate_data_source_creation(self):
        """Test creating a climate data source."""
        source = ClimateDataSource.objects.create(
            name='Test GEE Source',
            source_type='gee',
            description='Test Google Earth Engine source',
            api_endpoint='https://earthengine.googleapis.com',
            requires_authentication=True,
            spatial_resolution_m=1000.0,
            temporal_resolution_days=1.0,
            global_coverage=True,
            )
        
        self.assertEqual(source.name, 'Test GEE Source')
        self.assertEqual(source.source_type, 'gee')
        self.assertTrue(source.is_active)
        self.assertTrue(source.global_coverage)
        self.assertEqual(str(source), 'Test GEE Source (Google Earth Engine)')
    
    def test_climate_variable_creation(self):
        """Test creating climate variables."""
        variable = ClimateVariable.objects.create(
            name='temperature_2m',
            display_name='2m Temperature',
            description='Temperature at 2 metres above ground',
            category='temperature',
            unit='degrees Celsius',
            unit_symbol='°C',
            min_value=-50.0,
            max_value=50.0,
            health_relevance='Temperature affects heat-related health outcomes'
        )
        
        self.assertEqual(variable.name, 'temperature_2m')
        self.assertEqual(variable.category, 'temperature')
        self.assertEqual(str(variable), '2m Temperature (°C)')
        self.assertTrue(variable.supports_temporal_aggregation)
        self.assertEqual(variable.default_aggregation_method, 'mean')
    
    def test_climate_variable_mapping(self):
        """Test variable mapping between sources and variables."""
        source = ClimateDataSource.objects.create(
            name='GEE Source',
            source_type='gee',
            description='Test source',
            )
        
        variable = ClimateVariable.objects.create(
            name='temperature_2m',
            display_name='2m Temperature',
            category='temperature',
            unit='degrees Celsius',
            unit_symbol='°C'
        )
        
        mapping = ClimateVariableMapping.objects.create(
            variable=variable,
            data_source=source,
            source_variable_name='temperature_2m_mean',
            source_dataset='ECMWF/ERA5_LAND/DAILY_AGGR',
            scale_factor=1.0,
            offset=-273.15  # Convert Kelvin to Celsius
        )
        
        self.assertEqual(mapping.variable, variable)
        self.assertEqual(mapping.data_source, source)
        self.assertEqual(mapping.offset, -273.15)
        self.assertEqual(str(mapping), 'temperature_2m in GEE Source')
    
    def test_climate_data_request_creation(self):
        """Test creating a climate data request."""
        source = ClimateDataSource.objects.create(
            name='Test Source',
            source_type='gee',
            description='Test',
            )
        
        variable = ClimateVariable.objects.create(
            name='temperature',
            display_name='Temperature',
            category='temperature',
            unit='°C',
            unit_symbol='°C'
        )
        
        request = ClimateDataRequest.objects.create(
            study=self.study,
            data_source=source,
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            temporal_aggregation='monthly',
            spatial_buffer_km=5.0,
        )
        
        request.variables.add(variable)
        request.locations.add(self.location)
        
        self.assertEqual(request.study, self.study)
        self.assertEqual(request.status, 'pending')
        self.assertEqual(request.progress_percentage, 0)
        self.assertTrue('Test Study' in str(request))
    
    def test_climate_data_cache(self):
        """Test climate data caching functionality."""
        source = ClimateDataSource.objects.create(
            name='Test Source',
            source_type='gee',
            description='Test',
            )
        
        variable = ClimateVariable.objects.create(
            name='temperature',
            display_name='Temperature',
            category='temperature',
            unit='°C',
            unit_symbol='°C'
        )
        
        cache_entry = ClimateDataCache.objects.create(
            data_source=source,
            variable=variable,
            location=self.location,
            date=date(2023, 6, 15),
            value=25.5,
            quality_flag='good'
        )
        
        self.assertEqual(cache_entry.value, 25.5)
        self.assertEqual(cache_entry.quality_flag, 'good')
        self.assertFalse(cache_entry.is_expired)
        self.assertEqual(cache_entry.hit_count, 0)


class ClimateServicesTestCase(TestCase):
    """Test climate data services."""
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
        
        self.project = Project.objects.create(
            name='Test Project',
            created_by=self.user
        )
        
        self.study = Study.objects.create(
            name='Test Study',
            created_by=self.user,
            project=self.project,
            study_type='cohort',
            needs_climate_linkage=True
        )
        
        self.location = Location.objects.create(
            name='Johannesburg',
            latitude=-26.2041,
            longitude=28.0473
        )
        
        self.source = ClimateDataSource.objects.create(
            name='GEE Test',
            source_type='gee',
            description='Test GEE source',
            )
        
        self.variable = ClimateVariable.objects.create(
            name='temperature_2m',
            display_name='2m Temperature',
            category='temperature',
            unit='degrees Celsius',
            unit_symbol='°C'
        )
        
        # Create mapping
        ClimateVariableMapping.objects.create(
            variable=self.variable,
            data_source=self.source,
            source_variable_name='temperature_2m',
            scale_factor=1.0,
            offset=0.0
        )
    
    def test_gee_data_service_validation(self):
        """Test Earth Engine data service location validation."""
        service = EarthEngineDataService(self.source)
        
        # Valid location
        self.assertTrue(service.validate_location(self.location))
        
        # Invalid location (missing coordinates)
        invalid_location = Location.objects.create(name='Invalid')
        self.assertFalse(service.validate_location(invalid_location))
    
    def test_gee_data_service_date_validation(self):
        """Test Earth Engine data service date range validation."""
        service = EarthEngineDataService(self.source)
        
        start_date = datetime(2023, 1, 1)
        end_date = datetime(2023, 12, 31)
        
        # Valid date range
        self.assertTrue(service.validate_date_range(start_date, end_date))
        
        # Invalid date range (start after end)
        self.assertFalse(service.validate_date_range(end_date, start_date))
    
    def test_gee_data_service_fetch_data(self):
        """Test fetching data from Earth Engine service (mock implementation)."""
        service = EarthEngineDataService(self.source)
        
        start_date = datetime(2023, 6, 1)
        end_date = datetime(2023, 6, 3)
        
        data = service.fetch_data(
            variable=self.variable,
            location=self.location,
            start_date=start_date,
            end_date=end_date
        )
        
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 3)  # 3 days of data
        
        for item in data:
            self.assertIn('date', item)
            self.assertIn('value', item)
            self.assertIn('quality_flag', item)
            self.assertIn('source', item)
    
    def test_spatio_temporal_matcher(self):
        """Test spatial-temporal matching functionality."""
        matcher = SpatioTemporalMatcher()
        
        locations = [self.location]
        matched = matcher.match_study_locations(locations, buffer_km=5.0)
        
        self.assertEqual(len(matched), 1)
        location, metadata = matched[0]
        self.assertEqual(location, self.location)
        self.assertEqual(metadata['buffer_km'], 5.0)
        self.assertIn('bounds', metadata)
    
    def test_climate_data_processor(self):
        """Test climate data processing workflow."""
        # Create a climate request
        request = ClimateDataRequest.objects.create(
            study=self.study,
            data_source=self.source,
            start_date=date(2023, 6, 1),
            end_date=date(2023, 6, 3),
            temporal_aggregation='none',
            total_locations=1
        )
        request.variables.add(self.variable)
        request.locations.add(self.location)
        
        # Process the request
        processor = ClimateDataProcessor(request)
        result = processor.process_request()
        
        # Check results
        self.assertEqual(result['status'], 'success')
        self.assertGreater(result['total_observations'], 0)
        
        # Refresh request from database
        request.refresh_from_db()
        self.assertEqual(request.status, 'completed')
        self.assertEqual(request.processed_locations, 1)


class ClimateViewsTestCase(TestCase):
    """Test climate views and UI functionality."""
    
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
        
        self.project = Project.objects.create(
            name='Test Project',
            created_by=self.user
        )
        
        self.study = Study.objects.create(
            name='Test Study',
            created_by=self.user,
            project=self.project,
            study_type='cohort',
            needs_climate_linkage=True
        )
        
        self.location = Location.objects.create(
            name='Test Location',
            latitude=-26.2041,
            longitude=28.0473
        )
        
        # Create an observation so the location shows up in study queries
        attribute = Attribute.objects.create(
            variable_name='test_var',
            display_name='Test Variable',
            variable_type='float',
            category='health'
        )
        attribute.studies.add(self.study)
        
        time_dim = TimeDimension.objects.create(
            timestamp=timezone.now()
        )
        
        Observation.objects.create(
            location=self.location,
            attribute=attribute,
            time=time_dim,
            float_value=1.0
        )
        
        self.source = ClimateDataSource.objects.create(
            name='Test Source',
            source_type='gee',
            description='Test source',
            is_active=True,
            )
        
        self.variable = ClimateVariable.objects.create(
            name='temperature',
            display_name='Temperature',
            category='temperature',
            unit='°C',
            unit_symbol='°C'
        )
        
        # Create variable mapping for the test
        from .models import ClimateVariableMapping
        ClimateVariableMapping.objects.create(
            variable=self.variable,
            data_source=self.source,
            source_variable_name='temperature_2m'
        )
        
        self.client.login(email='test@example.com', password='testpass123')
    
    def test_climate_configuration_view_access(self):
        """Test accessing climate configuration view."""
        url = reverse('climate:configure', kwargs={'study_id': self.study.pk})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Climate Data Configuration')
        self.assertContains(response, self.study.name)
    
    def test_climate_configuration_view_no_locations(self):
        """Test configuration view when study has no locations."""
        # Create study without locations
        study_no_locations = Study.objects.create(
            name='No Locations Study',
            created_by=self.user,
            project=self.project,
            study_type='cohort',
            needs_climate_linkage=True
        )
        
        url = reverse('climate:configure', kwargs={'study_id': study_no_locations.pk})
        response = self.client.get(url)
        
        # Should redirect with error message
        self.assertEqual(response.status_code, 302)
    
    def test_climate_configuration_form_submission(self):
        """Test submitting climate configuration form."""
        url = reverse('climate:configure', kwargs={'study_id': self.study.pk})
        
        form_data = {
            'data_source': self.source.pk,
            'variables': [self.variable.pk],
            'start_date': '2023-01-01',
            'end_date': '2023-12-31',
            'temporal_aggregation': 'monthly',
            'spatial_buffer_km': 0.0,
        }
        
        response = self.client.post(url, form_data)
        
        # Should create request and redirect
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ClimateDataRequest.objects.filter(study=self.study).exists())
    
    def test_climate_request_list_view(self):
        """Test climate request list view."""
        # Create a request
        request = ClimateDataRequest.objects.create(
            study=self.study,
            data_source=self.source,
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
        )
        
        url = reverse('climate:request_list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Climate Data Requests')
        self.assertContains(response, self.study.name)
    
    def test_climate_request_detail_view(self):
        """Test climate request detail view."""
        request = ClimateDataRequest.objects.create(
            study=self.study,
            data_source=self.source,
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
        )
        
        url = reverse('climate:request_detail', kwargs={'pk': request.pk})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Request Status')
        self.assertContains(response, self.study.name)


class ClimateAPITestCase(APITestCase):
    """Test climate API endpoints."""
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
        self.client.force_authenticate(user=self.user)
        
        self.project = Project.objects.create(
            name='Test Project',
            created_by=self.user
        )
        
        self.study = Study.objects.create(
            name='Test Study',
            created_by=self.user,
            project=self.project,
            study_type='cohort',
            needs_climate_linkage=True
        )
        
        self.source = ClimateDataSource.objects.create(
            name='Test Source',
            source_type='gee',
            description='Test source',
            is_active=True,
            )
        
        self.variable = ClimateVariable.objects.create(
            name='temperature',
            display_name='Temperature',
            category='temperature',
            unit='°C',
            unit_symbol='°C'
        )
    
    def test_list_climate_sources_api(self):
        """Test listing climate data sources via API."""
        url = reverse('api:climate_api:source-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], 'Test Source')
    
    def test_list_climate_variables_api(self):
        """Test listing climate variables via API."""
        url = reverse('api:climate_api:variable-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['display_name'], 'Temperature')
    
    def test_list_variables_for_source_api(self):
        """Test getting variables for a specific source."""
        # Add variable to source
        ClimateVariableMapping.objects.create(
            variable=self.variable,
            data_source=self.source,
            source_variable_name='temp'
        )
        
        url = reverse('api:climate_api:source-variables', kwargs={'source_id': self.source.pk})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('source', response.data)
        self.assertIn('variables', response.data)
        self.assertEqual(len(response.data['variables']), 1)
    
    def test_create_climate_request_api(self):
        """Test creating climate data request via API."""
        url = reverse('api:climate_api:request-list')
        
        data = {
            'study': self.study.pk,
            'data_source': self.source.pk,
            'variable_ids': [self.variable.pk],
            'start_date': '2023-01-01',
            'end_date': '2023-12-31',
            'temporal_aggregation': 'monthly',
            'spatial_buffer_km': 0.0,
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(ClimateDataRequest.objects.filter(study=self.study).exists())
    
    def test_unauthenticated_api_access(self):
        """Test that unauthenticated users cannot access API."""
        self.client.force_authenticate(user=None)
        
        url = reverse('api:climate_api:source-list')
        response = self.client.get(url)
        
        # Should return 403 Forbidden (standard DRF behavior for IsAuthenticated)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class ClimateFormsTestCase(TestCase):
    """Test climate forms validation."""
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
        
        self.project = Project.objects.create(
            name='Test Project',
            created_by=self.user
        )
        
        self.study = Study.objects.create(
            name='Test Study',
            created_by=self.user,
            project=self.project,
            study_type='cohort',
            needs_climate_linkage=True,
            study_period_start=date(2023, 1, 1),
            study_period_end=date(2023, 12, 31)
        )
        
        self.source = ClimateDataSource.objects.create(
            name='Test Source',
            source_type='gee',
            description='Test source',
            is_active=True,
            data_start_date=date(2020, 1, 1),
            data_end_date=date(2024, 12, 31),
            created_by=self.user
        )
        
        self.variable = ClimateVariable.objects.create(
            name='temperature',
            display_name='Temperature',
            category='temperature',
            unit='°C',
            unit_symbol='°C'
        )
        
        # Create variable mapping
        ClimateVariableMapping.objects.create(
            variable=self.variable,
            data_source=self.source,
            source_variable_name='temperature_2m'
        )
    
    def test_climate_configuration_form_valid(self):
        """Test valid climate configuration form."""
        from .forms import ClimateDataConfigurationForm
        
        form_data = {
            'data_source': self.source.pk,
            'variables': [self.variable.pk],
            'start_date': date(2023, 6, 1),
            'end_date': date(2023, 8, 31),
            'temporal_aggregation': 'monthly',
            'spatial_buffer_km': 5.0,
        }
        
        form = ClimateDataConfigurationForm(
            data=form_data,
            study=self.study,
            user=self.user
        )
        
        self.assertTrue(form.is_valid(), f"Form errors: {form.errors}")
    
    def test_climate_configuration_form_invalid_dates(self):
        """Test climate configuration form with invalid date range."""
        from .forms import ClimateDataConfigurationForm
        
        form_data = {
            'data_source': self.source.pk,
            'variables': [self.variable.pk],
            'start_date': date(2023, 12, 1),  # After end date
            'end_date': date(2023, 6, 1),
            'temporal_aggregation': 'monthly',
            'spatial_buffer_km': 0.0,
        }
        
        form = ClimateDataConfigurationForm(
            data=form_data,
            study=self.study,
            user=self.user
        )
        
        self.assertFalse(form.is_valid())
        self.assertIn('Start date must be before end date', str(form.errors))


class ClimateIntegrationTestCase(TestCase):
    """Integration tests for complete climate workflows."""
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
        
        self.project = Project.objects.create(
            name='Climate Integration Test',
            created_by=self.user
        )
        
        self.study = Study.objects.create(
            name='Integration Study',
            created_by=self.user,
            project=self.project,
            study_type='cohort',
            needs_climate_linkage=True
        )
        
        # Create locations with coordinates
        self.locations = []
        for i in range(3):
            location = Location.objects.create(
                name=f'Location {i+1}',
                latitude=-26.2041 + i * 0.1,
                longitude=28.0473 + i * 0.1
            )
            self.locations.append(location)
        
        # Create study attributes and observations
        attribute = Attribute.objects.create(
            variable_name='health_outcome',
            display_name='Health Outcome',
            variable_type='float',
            category='health'
        )
        attribute.studies.add(self.study)
        
        time_dim = TimeDimension.objects.create(
            timestamp=timezone.now()
        )
        
        for location in self.locations:
            Observation.objects.create(
                location=location,
                attribute=attribute,
                time=time_dim,
                float_value=float(location.pk)
            )
        
        # Set up climate data source and variables
        self.source = ClimateDataSource.objects.create(
            name='Integration Test Source',
            source_type='gee',
            description='Test source for integration',
            is_active=True,
            )
        
        self.temp_var = ClimateVariable.objects.create(
            name='temperature_2m',
            display_name='2m Temperature',
            category='temperature',
            unit='degrees Celsius',
            unit_symbol='°C'
        )
        
        self.precip_var = ClimateVariable.objects.create(
            name='precipitation',
            display_name='Total Precipitation',
            category='precipitation',
            unit='millimetres',
            unit_symbol='mm'
        )
        
        # Create variable mappings
        for var in [self.temp_var, self.precip_var]:
            ClimateVariableMapping.objects.create(
                variable=var,
                data_source=self.source,
                source_variable_name=var.name,
                scale_factor=1.0,
                offset=0.0
            )
    
    def test_complete_climate_workflow(self):
        """Test complete climate data integration workflow."""
        # Step 1: Create climate data request
        request = ClimateDataRequest.objects.create(
            study=self.study,
            data_source=self.source,
            start_date=date(2023, 6, 1),
            end_date=date(2023, 6, 7),  # 7 days
            temporal_aggregation='none',
            spatial_buffer_km=0.0,
            total_locations=len(self.locations)
        )
        
        # Add variables and locations
        request.variables.set([self.temp_var, self.precip_var])
        request.locations.set(self.locations)
        
        # Step 2: Process the request
        processor = ClimateDataProcessor(request)
        result = processor.process_request()
        
        # Step 3: Verify results
        self.assertEqual(result['status'], 'success')
        
        # Refresh request
        request.refresh_from_db()
        self.assertEqual(request.status, 'completed')
        self.assertEqual(request.processed_locations, len(self.locations))
        self.assertGreater(request.total_observations, 0)
        
        # Step 4: Verify climate observations were created
        climate_attributes = Attribute.objects.filter(
            category='climate',
            variable_name__in=['climate_temperature_2m', 'climate_precipitation']
        )
        
        self.assertEqual(climate_attributes.count(), 2)
        
        total_observations = Observation.objects.filter(
            attribute__in=climate_attributes,
            location__in=self.locations
        ).count()
        
        # Should have: 3 locations × 2 variables × 7 days = 42 observations
        expected_observations = len(self.locations) * 2 * 7
        self.assertEqual(total_observations, expected_observations)
    
    def test_climate_data_caching(self):
        """Test that climate data is properly cached and reused."""
        # Create and process first request
        request1 = ClimateDataRequest.objects.create(
            study=self.study,
            data_source=self.source,
            start_date=date(2023, 6, 1),
            end_date=date(2023, 6, 3),
        )
        request1.variables.add(self.temp_var)
        request1.locations.set([self.locations[0]])
        
        processor1 = ClimateDataProcessor(request1)
        result1 = processor1.process_request()
        
        # Verify cache entries were created
        cache_count_after_first = ClimateDataCache.objects.count()
        self.assertGreater(cache_count_after_first, 0)
        
        # Create second request for same data
        request2 = ClimateDataRequest.objects.create(
            study=self.study,
            data_source=self.source,
            start_date=date(2023, 6, 1),
            end_date=date(2023, 6, 3),
        )
        request2.variables.add(self.temp_var)
        request2.locations.set([self.locations[0]])
        
        processor2 = ClimateDataProcessor(request2)
        result2 = processor2.process_request()
        
        # Cache count should be the same (reused existing entries)
        cache_count_after_second = ClimateDataCache.objects.count()
        self.assertEqual(cache_count_after_first, cache_count_after_second)
        
        # But hit counts should have increased
        cache_entries = ClimateDataCache.objects.filter(hit_count__gt=0)
        self.assertGreater(cache_entries.count(), 0)
