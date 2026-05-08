# Google Earth Engine API Setup

This document explains how to configure Google Earth Engine (GEE) API credentials for the HarmonAIze climate module.

## Overview

The climate module always uses live Google Earth Engine (GEE) data. Configure valid
credentials to enable API access in every environment.

TLDR; for local Docker development, the simplest workflow is:

1. Download the service account JSON key
2. Save it somewhere under the repository's local-only env directory, for example `./.envs/.local/gee-credentials.json`
3. Set `GOOGLE_APPLICATION_CREDENTIALS=/app/.envs/.local/gee-credentials.json` in `./.envs/.local/.django`

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

#### Local Docker setup

1. Save the downloaded JSON key to a local-only path inside the repo, for example:

```bash
mv ~/Downloads/gee-credentials.json ./.envs/.local/gee-credentials.json
```

2. Add the credential path to `./.envs/.local/.django`:

```bash
# Google Earth Engine Configuration
GOOGLE_APPLICATION_CREDENTIALS=/app/.envs/.local/gee-credentials.json
```

3. Restart the containers so Django picks up the new environment variable:

```bash
docker-compose -f docker-compose.local.yml restart django celeryworker celerybeat flower
```

Because the repository is mounted into the Django container at `/app`, any file you keep under the local ignored env directory can be referenced from there.

#### Other environments

Set `GOOGLE_APPLICATION_CREDENTIALS` in the relevant environment file, such as `.envs/.production/.django`, to a path that exists inside the running container.

### 6. Update .gitignore

Ensure these files are ignored:

```gitignore
.envs/.local/.django
.envs/.production/.django
```

Files stored under `.envs/.local/` are already kept out of version control in normal local development. Do not commit service account JSON files anywhere in the repository.

### 7. Install Python Dependencies

The Earth Engine Python API is already included in the project requirements, so no extra local package installation should be necessary when running through Docker.

Relevant packages in `requirements/base.txt` include:

```bash
earthengine-api==0.1.XXX
google-auth==2.XX.X
google-auth-oauthlib==1.XX.X
google-auth-httplib2==0.2.X
```

### 8. Test the Connection

Run this through Docker to verify credentials work:

```bash
docker-compose -f docker-compose.local.yml run --rm django python manage.py shell
```

Then run:

```python

from climate.services import EarthEngineDataService
from climate.models import ClimateDataSource

# Create a test data source
source = ClimateDataSource.objects.first()  # or create one
service = EarthEngineDataService(source)

# This should initialize without errors
print("✓ Google Earth Engine initialized successfully")
```

## Settings Configuration

### Django Settings

Ensure `GOOGLE_APPLICATION_CREDENTIALS` is available in your environment so
`EarthEngineDataService` can initialize. No fallback mode is available.

## Available GEE Datasets

The climate module currently supports these datasets:

### ERA5 (via GEE)
  - Temperature: `mean_2m_air_temperature`
  - Precipitation: `total_precipitation`
  - Humidity, pressure, wind, etc.

### CHIRPS (Precipitation)

### MODIS (Vegetation/Land Surface)

## Troubleshooting

### Issue: "Earth Engine not initialized"

**Solution:**
```bash
# Check the file exists inside the Django container
docker-compose -f docker-compose.local.yml run --rm django ls -la $GOOGLE_APPLICATION_CREDENTIALS

# Verify the environment variable is set in the container
docker-compose -f docker-compose.local.yml run --rm django env | grep GOOGLE_APPLICATION_CREDENTIALS

# Check file permissions
chmod 600 ./.envs/.local/gee-credentials.json #or DOS equivalent
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

## Cost Considerations

  - Compute units per day
  - Storage limits
  - Concurrent requests

Monitor usage at: https://code.earthengine.google.com/

## Security Best Practices

1. ✅ **DO**: Use service accounts (not user accounts)
2. ✅ **DO**: Keep credentials in a local-only or managed secret location
3. ✅ **DO**: Rotate credentials regularly
4. ✅ **DO**: Use least-privilege IAM roles
5. ❌ **DON'T**: Commit credentials to git
6. ❌ **DON'T**: Share credentials files
7. ❌ **DON'T**: Use production credentials in development
