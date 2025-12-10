"""
Tests for climate API endpoints.

This module tests the JSON API endpoints added for triggering and monitoring
climate data processing.
"""
import json
from datetime import date
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse

from core.models import Project, Study, Location, TimeDimension, Attribute, Observation
from .models import (
    ClimateDataSource,
    ClimateVariable,
    ClimateVariableMapping,
    ClimateDataRequest,
)

User = get_user_model()


class ClimateAPIEndpointsTestCase(TestCase):
    """Test the new JSON API endpoints for climate data processing."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()
        self.user = User.objects.create_user(
            email='test@example.com',
            password='testpass123',
            name='Test User'
        )
        self.client.login(email='test@example.com', password='testpass123')

        # Create project and study
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

        # Create location
        self.location = Location.objects.create(
            name='Test Location',
            latitude=-26.2041,
            longitude=28.0473
        )

        # Create data source
        self.source = ClimateDataSource.objects.create(
            name='Test GEE Source',
            source_type='gee',
            description='Test Google Earth Engine source',
            is_active=True,
        )

        # Create variables
        self.temp_var = ClimateVariable.objects.create(
            name='temperature_2m',
            display_name='2m Temperature',
            description='Air temperature at 2 meters',
            category='temperature',
            unit='degrees Celsius',
            unit_symbol='°C',
            min_value=-50,
            max_value=50,
        )

        self.precip_var = ClimateVariable.objects.create(
            name='precipitation',
            display_name='Total Precipitation',
            description='Total precipitation amount',
            category='precipitation',
            unit='millimeters',
            unit_symbol='mm',
            min_value=0,
            max_value=500,
        )

        # Create variable mappings
        for var in [self.temp_var, self.precip_var]:
            ClimateVariableMapping.objects.create(
                variable=var,
                data_source=self.source,
                source_variable_name=var.name,
                source_dataset='ECMWF/ERA5/DAILY',
                source_band=var.name,
                scale_factor=1.0,
                offset=0.0
            )

    def test_data_sources_api_requires_login(self):
        """Test that data sources API requires authentication."""
        self.client.logout()
        url = reverse('climate:data_sources_api')
        response = self.client.get(url)
        # Should redirect to login or return 302
        self.assertIn(response.status_code, [302, 403])

    def test_data_sources_api_returns_sources(self):
        """Test data sources API returns active sources."""
        url = reverse('climate:data_sources_api')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIn('sources', data)
        self.assertEqual(len(data['sources']), 1)
        self.assertEqual(data['sources'][0]['name'], 'Test GEE Source')
        self.assertEqual(data['sources'][0]['source_type'], 'gee')
        self.assertTrue(data['sources'][0]['global_coverage'])

    def test_variables_api_returns_all_variables(self):
        """Test variables API returns all variables."""
        url = reverse('climate:variables_api')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIn('variables', data)
        self.assertIn('categories', data)
        self.assertEqual(len(data['variables']), 2)

        # Check variable data
        var_names = [v['display_name'] for v in data['variables']]
        self.assertIn('2m Temperature', var_names)
        self.assertIn('Total Precipitation', var_names)

        # Check categories are provided
        self.assertGreater(len(data['categories']), 0)
        category_values = [c['value'] for c in data['categories']]
        self.assertIn('temperature', category_values)
        self.assertIn('precipitation', category_values)

    def test_variables_api_filter_by_category(self):
        """Test filtering variables by category."""
        url = reverse('climate:variables_api') + '?category=temperature'
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(len(data['variables']), 1)
        self.assertEqual(data['variables'][0]['display_name'], '2m Temperature')
        self.assertEqual(data['variables'][0]['category'], 'temperature')

    def test_variables_api_filter_by_source(self):
        """Test filtering variables by data source."""
        url = reverse('climate:variables_api') + f'?source_id={self.source.id}'
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Both variables should be returned (both mapped to this source)
        self.assertEqual(len(data['variables']), 2)

    def test_process_request_api_requires_post(self):
        """Test that process request API requires POST method."""
        # Create a climate request
        request = ClimateDataRequest.objects.create(
            study=self.study,
            data_source=self.source,
            start_date=date(2023, 6, 1),
            end_date=date(2023, 6, 3),
        )
        request.variables.add(self.temp_var)
        request.locations.add(self.location)

        url = reverse('climate:process_request_api', kwargs={'request_id': request.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 405)  # Method not allowed
        data = response.json()
        self.assertIn('error', data)

    def test_process_request_api_success(self):
        """Test successful processing of climate request."""
        # Create a climate request
        request = ClimateDataRequest.objects.create(
            study=self.study,
            data_source=self.source,
            start_date=date(2023, 6, 1),
            end_date=date(2023, 6, 3),
            total_locations=1
        )
        request.variables.add(self.temp_var)
        request.locations.add(self.location)

        url = reverse('climate:process_request_api', kwargs={'request_id': request.id})
        response = self.client.post(url)

        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Should either be 'started' (Celery) or 'completed' (sync)
        self.assertIn(data['status'], ['started', 'completed'])
        self.assertEqual(data['request_id'], request.id)

        if data['status'] == 'started':
            self.assertIn('task_id', data)
        elif data['status'] == 'completed':
            self.assertIn('result', data)

    def test_process_request_api_already_processing(self):
        """Test that processing API handles already processing requests."""
        # Create a climate request that's already processing
        request = ClimateDataRequest.objects.create(
            study=self.study,
            data_source=self.source,
            start_date=date(2023, 6, 1),
            end_date=date(2023, 6, 3),
            status='processing'
        )

        url = reverse('climate:process_request_api', kwargs={'request_id': request.id})
        response = self.client.post(url)

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data['status'], 'processing')
        self.assertIn('already processing', data['message'].lower())

    def test_process_request_api_unauthorized_access(self):
        """Test that users cannot process requests for studies they don't own."""
        # Create another user
        other_user = User.objects.create_user(
            email='other@example.com',
            password='otherpass',
            name='Other User'
        )

        # Create project and study for other user
        other_project = Project.objects.create(
            name='Other Project',
            created_by=other_user
        )

        other_study = Study.objects.create(
            name='Other Study',
            created_by=other_user,
            project=other_project,
            study_type='cohort'
        )

        # Create request for other user's study
        request = ClimateDataRequest.objects.create(
            study=other_study,
            data_source=self.source,
            start_date=date(2023, 6, 1),
            end_date=date(2023, 6, 3),
        )

        # Try to process with our user
        url = reverse('climate:process_request_api', kwargs={'request_id': request.id})
        response = self.client.post(url)

        # Should return 404 or permission denied
        self.assertEqual(response.status_code, 404)

    def test_request_status_api(self):
        """Test request status API returns correct information."""
        # Create a climate request
        request = ClimateDataRequest.objects.create(
            study=self.study,
            data_source=self.source,
            start_date=date(2023, 6, 1),
            end_date=date(2023, 6, 3),
            status='processing',
            total_locations=5,
            processed_locations=3,
            total_observations=15
        )

        url = reverse('climate:request_status_api', kwargs={'request_id': request.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data['request_id'], request.id)
        self.assertEqual(data['status'], 'processing')
        self.assertEqual(data['total_locations'], 5)
        self.assertEqual(data['processed_locations'], 3)
        self.assertEqual(data['total_observations'], 15)
        self.assertEqual(data['progress_percentage'], 60)  # 3/5 * 100

    def test_request_status_api_unauthorized(self):
        """Test that users cannot view status for requests they don't own."""
        # Create another user and their request
        other_user = User.objects.create_user(
            email='other@example.com',
            password='otherpass',
            name='Other User'
        )

        other_project = Project.objects.create(
            name='Other Project',
            created_by=other_user
        )

        other_study = Study.objects.create(
            name='Other Study',
            created_by=other_user,
            project=other_project,
            study_type='cohort'
        )

        request = ClimateDataRequest.objects.create(
            study=other_study,
            data_source=self.source,
            start_date=date(2023, 6, 1),
            end_date=date(2023, 6, 3),
        )

        # Try to view status with our user
        url = reverse('climate:request_status_api', kwargs={'request_id': request.id})
        response = self.client.get(url)

        # Should return 404
        self.assertEqual(response.status_code, 404)

    def test_request_status_api_nonexistent_request(self):
        """Test status API with non-existent request ID."""
        url = reverse('climate:request_status_api', kwargs={'request_id': 99999})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertIn('error', data)


class ClimateAPIIntegrationTestCase(TestCase):
    """Integration tests for climate API workflow."""

    def setUp(self):
        """Set up test data."""
        self.client = Client()
        self.user = User.objects.create_user(
            email='integration@example.com',
            password='testpass123',
            name='Integration User'
        )
        self.client.login(email='integration@example.com', password='testpass123')

        # Create complete test environment
        self.project = Project.objects.create(
            name='Integration Project',
            created_by=self.user
        )

        self.study = Study.objects.create(
            name='Integration Study',
            created_by=self.user,
            project=self.project,
            study_type='cohort',
            needs_climate_linkage=True
        )

        # Create locations
        self.locations = []
        for i in range(3):
            location = Location.objects.create(
                name=f'Location {i+1}',
                latitude=-26.2041 + i * 0.1,
                longitude=28.0473 + i * 0.1
            )
            self.locations.append(location)

        # Create data source
        self.source = ClimateDataSource.objects.create(
            name='Integration Source',
            source_type='gee',
            description='Source for integration testing',
            is_active=True,
        )

        # Create variable
        self.variable = ClimateVariable.objects.create(
            name='temperature_2m',
            display_name='2m Temperature',
            category='temperature',
            unit='degrees Celsius',
            unit_symbol='°C',
        )

        # Create mapping
        ClimateVariableMapping.objects.create(
            variable=self.variable,
            data_source=self.source,
            source_variable_name='temperature_2m',
            source_dataset='ECMWF/ERA5/DAILY',
            scale_factor=1.0,
            offset=0.0
        )

    def test_complete_api_workflow(self):
        """Test complete workflow: create request → process → check status."""
        # Step 1: Get available data sources
        sources_response = self.client.get(reverse('climate:data_sources_api'))
        self.assertEqual(sources_response.status_code, 200)
        sources_data = sources_response.json()
        self.assertGreater(len(sources_data['sources']), 0)

        # Step 2: Get available variables
        variables_response = self.client.get(reverse('climate:variables_api'))
        self.assertEqual(variables_response.status_code, 200)
        variables_data = variables_response.json()
        self.assertGreater(len(variables_data['variables']), 0)

        # Step 3: Create a climate request (simulating form submission)
        request = ClimateDataRequest.objects.create(
            study=self.study,
            data_source=self.source,
            start_date=date(2023, 6, 1),
            end_date=date(2023, 6, 3),
            total_locations=len(self.locations)
        )
        request.variables.add(self.variable)
        request.locations.set(self.locations)

        # Step 4: Trigger processing
        process_url = reverse('climate:process_request_api', kwargs={'request_id': request.id})
        process_response = self.client.post(process_url)
        self.assertEqual(process_response.status_code, 200)
        process_data = process_response.json()
        self.assertIn(process_data['status'], ['started', 'completed'])

        # Step 5: Check status
        status_url = reverse('climate:request_status_api', kwargs={'request_id': request.id})
        status_response = self.client.get(status_url)
        self.assertEqual(status_response.status_code, 200)
        status_data = status_response.json()

        self.assertEqual(status_data['request_id'], request.id)
        self.assertIn('status', status_data)
        self.assertIn('progress_percentage', status_data)

        # If processing completed synchronously, verify observations were created
        if process_data['status'] == 'completed':
            request.refresh_from_db()
            self.assertEqual(request.status, 'completed')
            self.assertGreater(request.total_observations, 0)
