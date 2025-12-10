# Google Earth Engine API Setup

This document explains how to configure Google Earth Engine (GEE) API credentials for the HarmonAIze climate module.

## Overview

The climate module supports two modes:
- **Mock Mode** (default): Uses simulated data for testing
- **Production Mode**: Makes real API calls to Google Earth Engine

## Prerequisites

1. A Google Cloud Platform (GCP) project
2. Google Earth Engine API enabled
3. Service account with Earth Engine permissions

## Step-by-Step Setup

### 1. Create a GCP Service Account

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Select or create a project
3. Navigate to **IAM & Admin** → **Service Accounts**
4. Click **Create Service Account**
5. Name it (e.g., `harmonaize-gee-service`)
6. Grant the role: **Earth Engine Resource Writer**
7. Click **Done**

### 2. Create and Download Service Account Key

1. Click on the service account you just created
2. Go to the **Keys** tab
3. Click **Add Key** → **Create New Key**
4. Choose **JSON** format
5. Click **Create**
6. Save the downloaded JSON file securely (e.g., `gee-credentials.json`)

**⚠️ IMPORTANT:** Never commit this file to version control!

### 3. Enable Google Earth Engine API

1. In Google Cloud Console, go to **APIs & Services** → **Library**
2. Search for "Earth Engine API"
3. Click **Enable**

### 4. Register for Earth Engine Access

1. Go to [Google Earth Engine](https://earthengine.google.com/)
2. Click **Sign Up**
3. Register your service account email
4. Wait for approval (usually automatic for service accounts)

### 5. Configure HarmonAIze

#### Option A: Using Environment Variables (Recommended for Production)

Add to your `.envs/.local/.django` or `.envs/.production/.django`:

```bash
# Google Earth Engine Configuration
GOOGLE_APPLICATION_CREDENTIALS=/path/to/gee-credentials.json
CLIMATE_USE_MOCK_DATA=False
```

#### Option B: Using Docker Secrets (Most Secure)

1. Create a secret file:
```bash
mkdir -p secrets
cp /path/to/gee-credentials.json secrets/gee-credentials.json
chmod 600 secrets/gee-credentials.json
```

2. Add to `.envs/.local/.django`:
```bash
GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/gee-credentials
CLIMATE_USE_MOCK_DATA=False
```

3. Update `docker-compose.local.yml`:
```yaml
services:
  django:
    secrets:
      - gee-credentials

secrets:
  gee-credentials:
    file: ./secrets/gee-credentials.json
```

#### Option C: Direct Path (Development Only)

Place credentials file in project and reference it:

```python
# In config/settings/local.py
import os
from pathlib import Path

# Path to GEE credentials (DO NOT commit this file!)
GEE_CREDENTIALS_PATH = Path(BASE_DIR) / 'secrets' / 'gee-credentials.json'

if GEE_CREDENTIALS_PATH.exists():
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(GEE_CREDENTIALS_PATH)
    CLIMATE_USE_MOCK_DATA = False
else:
    CLIMATE_USE_MOCK_DATA = True
```

### 6. Update .gitignore

Ensure these files are ignored:

```gitignore
# Google Earth Engine credentials
secrets/
gee-credentials.json
*-credentials.json
.envs/.local/.django
.envs/.production/.django
```

### 7. Install Python Dependencies

The Earth Engine Python API is already in `requirements/base.txt`:

```bash
earthengine-api==0.1.XXX
google-auth==2.XX.X
google-auth-oauthlib==1.XX.X
google-auth-httplib2==0.2.X
```

If not, add them and run:
```bash
pip install earthengine-api google-auth
```

### 8. Test the Connection

Run this in Django shell to verify credentials work:

```python
python manage.py shell

from climate.services import EarthEngineDataService
from climate.models import ClimateDataSource

# Create a test data source
source = ClimateDataSource.objects.first()  # or create one
service = EarthEngineDataService(source, use_mock=False)

# This should initialize without errors
print("✓ Google Earth Engine initialized successfully")
```

## Settings Configuration

### Django Settings

Add to `config/settings/base.py`:

```python
# Climate Module Settings
CLIMATE_USE_MOCK_DATA = env.bool('CLIMATE_USE_MOCK_DATA', default=True)
```

Add to `config/settings/production.py`:

```python
# Use real GEE data in production
CLIMATE_USE_MOCK_DATA = env.bool('CLIMATE_USE_MOCK_DATA', default=False)
```

## Available GEE Datasets

The climate module currently supports these datasets:

### ERA5 (via GEE)
- **Dataset**: `ECMWF/ERA5/DAILY` or `ECMWF/ERA5_LAND/DAILY_AGGR`
- **Variables**:
  - Temperature: `mean_2m_air_temperature`
  - Precipitation: `total_precipitation`
  - Humidity, pressure, wind, etc.
- **Resolution**: ~27km (ERA5), ~11km (ERA5-Land)
- **Temporal**: Daily, 1940-present

### CHIRPS (Precipitation)
- **Dataset**: `UCSB-CHG/CHIRPS/DAILY`
- **Variables**: `precipitation`
- **Resolution**: ~5.5km
- **Temporal**: Daily, 1981-present

### MODIS (Vegetation/Land Surface)
- **Dataset**: `MODIS/006/MOD11A1` (temperature), `MODIS/006/MOD13A2` (NDVI)
- **Variables**: Land surface temperature, NDVI, EVI
- **Resolution**: 1km (temperature), 1km (vegetation)
- **Temporal**: Daily/16-day, 2000-present

## Troubleshooting

### Issue: "Earth Engine not initialized"

**Solution:**
```bash
# Check credentials file exists
ls -la $GOOGLE_APPLICATION_CREDENTIALS

# Verify environment variable is set
echo $GOOGLE_APPLICATION_CREDENTIALS

# Check file permissions
chmod 600 /path/to/gee-credentials.json
```

### Issue: "Permission denied" or "Service account not registered"

**Solution:**
1. Ensure service account has Earth Engine Resource Writer role
2. Register service account at https://signup.earthengine.google.com/
3. Wait a few minutes for registration to propagate

### Issue: "Project ID not found"

**Solution:**
Ensure your credentials JSON contains `project_id`:
```json
{
  "type": "service_account",
  "project_id": "your-project-id",
  ...
}
```

### Issue: Mock data still being used

**Solution:**
1. Check `CLIMATE_USE_MOCK_DATA` is set to `False`
2. Restart Django/Celery containers
3. Check logs for Earth Engine initialization messages

## Cost Considerations

- Google Earth Engine is **free for research and educational use**
- Commercial use requires **Earth Engine Commercial license**
- There are quota limits:
  - Compute units per day
  - Storage limits
  - Concurrent requests

Monitor usage at: https://code.earthengine.google.com/

## Security Best Practices

1. ✅ **DO**: Use service accounts (not user accounts)
2. ✅ **DO**: Store credentials in secrets management system
3. ✅ **DO**: Rotate credentials regularly
4. ✅ **DO**: Use least-privilege IAM roles
5. ❌ **DON'T**: Commit credentials to git
6. ❌ **DON'T**: Share credentials files
7. ❌ **DON'T**: Use production credentials in development

## Alternative: Copernicus Climate Data Store (CDS)

For ERA5 data directly from Copernicus (not via GEE):

1. Register at: https://cds.climate.copernicus.eu/
2. Get API key from: https://cds.climate.copernicus.eu/api-how-to
3. Create `~/.cdsapirc`:
```
url: https://cds.climate.copernicus.eu/api/v2
key: {UID}:{API-KEY}
```

4. Install CDS API:
```bash
pip install cdsapi
```

5. Set in Django settings:
```python
CLIMATE_USE_COPERNICUS_CDS = True
```

## Support

For GEE-specific issues:
- [Earth Engine Forum](https://groups.google.com/g/google-earth-engine-developers)
- [Earth Engine Documentation](https://developers.google.com/earth-engine/)

For HarmonAIze climate module issues:
- Check module logs: `docker logs harmonaize_local_django`
- Review Celery logs: `docker logs harmonaize_local_celeryworker`
- See climate module tests: `python manage.py test climate`
