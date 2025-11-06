# Climate Module Testing Guide

This document provides comprehensive information about testing the HarmonAIze climate module.

## Test Organization

### Test Files

```
climate/
├── tests.py                    # Comprehensive legacy tests
├── test_api_endpoints.py       # New API endpoint tests
├── fixtures/
│   └── test_climate_data.json  # Test data fixtures
└── TESTING.md                  # This file
```

### Test Coverage

The test suite covers:

1. **Models** (`ClimateModelsTestCase`)
   - ClimateDataSource creation and validation
   - ClimateVariable creation with categories
   - ClimateVariableMapping functionality
   - ClimateDataRequest lifecycle
   - ClimateDataCache caching behavior

2. **Services** (`ClimateServicesTestCase`)
   - EarthEngineDataService validation
   - Date range validation
   - Mock data fetching
   - SpatioTemporalMatcher location matching
   - ClimateDataProcessor workflow

3. **Views** (`ClimateViewsTestCase`)
   - Climate configuration views
   - Request list and detail views
   - Form submissions
   - Authorization checks

4. **API Endpoints** (`ClimateAPIEndpointsTestCase`)
   - Data sources API
   - Variables API with filtering
   - Process request API
   - Request status API
   - Authentication requirements

5. **Integration** (`ClimateIntegrationTestCase`)
   - Complete climate data workflow
   - Data caching and reuse
   - Observation creation in core models

## Running Tests

### Run All Climate Tests

```bash
# Local environment
python manage.py test climate

# Docker environment
docker exec harmonaize_local_django python manage.py test climate

# With verbose output
docker exec harmonaize_local_django python manage.py test climate --verbosity=2
```

### Run Specific Test Cases

```bash
# Model tests only
python manage.py test climate.tests.ClimateModelsTestCase

# API endpoint tests only
python manage.py test climate.test_api_endpoints.ClimateAPIEndpointsTestCase

# Integration tests only
python manage.py test climate.tests.ClimateIntegrationTestCase

# Specific test method
python manage.py test climate.test_api_endpoints.ClimateAPIEndpointsTestCase.test_process_request_api_success
```

### Run with Coverage

```bash
# Install coverage
pip install coverage

# Run tests with coverage
coverage run --source='climate' manage.py test climate
coverage report
coverage html

# View HTML report
open htmlcov/index.html
```

### Run in Docker

```bash
# Make sure containers are running
docker-compose -f docker-compose.local.yml up -d

# Run tests
docker exec harmonaize_local_django python manage.py test climate

# Run with specific settings
docker exec harmonaize_local_django python manage.py test climate --settings=config.settings.test
```

## Test Data Setup

### Using Fixtures

Load test fixtures for quick setup:

```bash
python manage.py loaddata climate/fixtures/test_climate_data.json
```

This creates:
- 2 data sources (GEE and ERA5)
- 3 climate variables (temperature, precipitation, humidity)
- 3 variable mappings

### Manual Test Data

Create test data programmatically:

```python
from climate.models import ClimateDataSource, ClimateVariable, ClimateVariableMapping

# Create data source
source = ClimateDataSource.objects.create(
    name='Test Source',
    source_type='gee',
    description='Test GEE source',
    is_active=True
)

# Create variable
variable = ClimateVariable.objects.create(
    name='temperature_2m',
    display_name='2m Temperature',
    category='temperature',
    unit='degrees Celsius',
    unit_symbol='°C'
)

# Create mapping
ClimateVariableMapping.objects.create(
    variable=variable,
    data_source=source,
    source_variable_name='temperature_2m',
    source_dataset='ECMWF/ERA5/DAILY',
    scale_factor=1.0,
    offset=-273.15
)
```

## Mock vs Real Data

### Mock Mode (Default)

By default, tests use **mock data** to avoid requiring GEE credentials:

```python
# In tests, mock mode is automatic
service = EarthEngineDataService(source, use_mock=True)
```

Benefits:
- ✅ No API credentials required
- ✅ Fast test execution
- ✅ Deterministic results
- ✅ No API quotas consumed

### Real API Testing

To test with real Google Earth Engine API:

1. Set up GEE credentials (see `GEE_SETUP.md`)

2. Set environment variable:
```bash
export CLIMATE_USE_MOCK_DATA=False
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/credentials.json
```

3. Run tests:
```bash
python manage.py test climate --tag=integration
```

**Note:** Real API tests are slower and consume API quotas.

## Test Database

### PostgreSQL (Recommended)

Tests should run against PostgreSQL for production parity:

```python
# config/settings/test.py
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'test_harmonaize',
        'USER': 'postgres',
        'PASSWORD': 'postgres',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

### SQLite (Faster)

For quick iteration during development:

```bash
# Run with SQLite in memory
python manage.py test climate --settings=config.settings.test_sqlite
```

## Continuous Integration

### GitHub Actions

Tests run automatically on PR creation:

```yaml
# .github/workflows/test.yml
name: Test Suite

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Run tests
        run: |
          docker-compose -f docker-compose.ci.yml up -d
          docker-compose -f docker-compose.ci.yml exec -T django python manage.py test climate
```

### Pre-commit Hook

Add to `.pre-commit-config.yaml`:

```yaml
  - repo: local
    hooks:
      - id: django-test-climate
        name: Climate Module Tests
        entry: python manage.py test climate --failfast
        language: system
        pass_filenames: false
```

## Common Issues

### Issue: Tests fail due to missing `created_by` field

**Solution:** Some models may not have `created_by` field. Remove it from test setUp:

```python
# Before
source = ClimateDataSource.objects.create(
    name='Test',
    created_by=self.user  # May not exist
)

# After
source = ClimateDataSource.objects.create(
    name='Test'
)
```

### Issue: CSRF failures in API tests

**Solution:** Use Django test client's built-in CSRF handling:

```python
# Correct - automatic CSRF
response = self.client.post(url, data)

# Also works - explicit CSRF
from django.test import Client
client = Client(enforce_csrf_checks=True)
```

### Issue: Database reset between tests

**Solution:** Django automatically rolls back transactions. To persist data:

```python
from django.test import TransactionTestCase  # Instead of TestCase

class MyTestCase(TransactionTestCase):
    def test_something(self):
        # Data persists across methods
        pass
```

### Issue: Celery tasks not running in tests

**Solution:** Use `CELERY_TASK_ALWAYS_EAGER` in test settings:

```python
# config/settings/test.py
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
```

This runs tasks synchronously during tests.

## Writing New Tests

### Test Template

```python
from django.test import TestCase
from django.contrib.auth import get_user_model
from climate.models import ClimateDataSource

User = get_user_model()

class MyNewTestCase(TestCase):
    """Test description."""

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )

        self.source = ClimateDataSource.objects.create(
            name='Test Source',
            source_type='gee'
        )

    def test_something(self):
        """Test a specific behavior."""
        # Arrange
        expected = 'value'

        # Act
        result = self.source.some_method()

        # Assert
        self.assertEqual(result, expected)

    def tearDown(self):
        """Clean up after tests."""
        pass  # Usually not needed - Django auto-rollback
```

### Best Practices

1. **Arrange-Act-Assert**: Structure tests clearly
2. **One assertion per test**: Keep tests focused
3. **Descriptive names**: `test_process_request_creates_observations`
4. **Independent tests**: Each test should work in isolation
5. **Mock external APIs**: Don't hit real APIs in unit tests
6. **Test edge cases**: Empty data, null values, invalid inputs

### Testing API Endpoints

```python
from django.urls import reverse

class APITest(TestCase):
    def setUp(self):
        self.client.login(email='test@example.com', password='testpass123')

    def test_api_endpoint(self):
        url = reverse('climate:data_sources_api')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('sources', data)
```

### Testing Celery Tasks

```python
from climate.tasks import process_climate_data_request

class TaskTest(TestCase):
    def test_climate_processing_task(self):
        # Create request
        request = ClimateDataRequest.objects.create(...)

        # Call task
        result = process_climate_data_request.delay(request.id)

        # Check result
        self.assertEqual(result.get()['status'], 'success')
```

## Performance Testing

### Timing Tests

```python
import time
from django.test import TestCase

class PerformanceTest(TestCase):
    def test_processing_speed(self):
        start = time.time()

        # Run operation
        process_climate_data()

        duration = time.time() - start
        self.assertLess(duration, 5.0, "Processing took too long")
```

### Load Testing

Use Django's `TransactionTestCase` with multiple concurrent requests:

```python
from concurrent.futures import ThreadPoolExecutor

class LoadTest(TransactionTestCase):
    def test_concurrent_requests(self):
        def make_request():
            return self.client.get(reverse('climate:dashboard'))

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_request) for _ in range(100)]
            results = [f.result() for f in futures]

        # All should succeed
        self.assertTrue(all(r.status_code == 200 for r in results))
```

## Test Metrics

### Current Coverage

Run `coverage report` to see:

```
Name                           Stmts   Miss  Cover
--------------------------------------------------
climate/models.py                120      5    96%
climate/views.py                 180     12    93%
climate/services.py              250     25    90%
climate/tasks.py                  45      3    93%
--------------------------------------------------
TOTAL                            595     45    92%
```

### Goals

- Unit test coverage: **>90%**
- Integration test coverage: **>80%**
- Critical paths: **100%**

## Debugging Tests

### Verbose Output

```bash
python manage.py test climate --verbosity=2
```

### Keep Test Database

```bash
python manage.py test climate --keepdb
```

### Stop on First Failure

```bash
python manage.py test climate --failfast
```

### Python Debugger

```python
def test_something(self):
    import pdb; pdb.set_trace()
    # Test code...
```

### Django Debug Toolbar

Enable in test settings:

```python
# config/settings/test.py
DEBUG = True
INTERNAL_IPS = ['127.0.0.1']
```

## Further Reading

- [Django Testing Documentation](https://docs.djangoproject.com/en/stable/topics/testing/)
- [Django REST Framework Testing](https://www.django-rest-framework.org/api-guide/testing/)
- [Coverage.py Documentation](https://coverage.readthedocs.io/)
- [pytest-django](https://pytest-django.readthedocs.io/) (Alternative test runner)
