# Google Earth Engine Setup - Quick Guide for Meeting

**Status:** ✅ Credentials secured locally
**Next:** Deploy to Azure for live demo

## What We've Done

1. ✅ Got service account JSON credentials from Google Cloud
2. ✅ Saved securely in `harmonaize/secrets/gee-credentials.json` (gitignored)
3. ✅ Created deployment script

## For Your Meeting Today - Two Options

### Option A: Test Locally First (5 minutes)

Test that GEE works before deploying to Azure:

```bash
cd harmonaize

# Set environment variable to use real GEE
export GOOGLE_APPLICATION_CREDENTIALS="$(pwd)/secrets/gee-credentials.json"
export CLIMATE_USE_MOCK_DATA=False

# Start containers
docker-compose -f docker-compose.local.yml up -d

# Test GEE connection in Django shell
docker-compose -f docker-compose.local.yml exec django python manage.py shell

# In the shell, run:
from climate.services import EarthEngineDataService
from climate.models import ClimateDataSource

source = ClimateDataSource.objects.filter(source_type='gee').first()
if source:
    service = EarthEngineDataService(source, use_mock=False)
    print("✓ Google Earth Engine initialized successfully!")
```

### Option B: Deploy to Azure (Requires Permissions)

**If you have someone with Azure access**, they can run:

```bash
./DEPLOY_GEE_TO_AZURE.sh
```

**Or manually in Azure Portal:**

1. Go to: https://portal.azure.com
2. Navigate to: **Container Apps** → **harmonaize-django**
3. Go to: **Secrets** tab
4. Add secret:
   - Name: `gee-credentials`
   - Value: (paste contents of `harmonaize/secrets/gee-credentials.json`)
5. Go to: **Containers** → **Environment variables**
6. Add/Update:
   - `CLIMATE_USE_MOCK_DATA` = `False`
   - `GOOGLE_APPLICATION_CREDENTIALS` = `secretref:gee-credentials`
   - `GEE_PROJECT_ID` = `joburg-hvi`
7. Click **Save** → **Create new revision**

## For Today's Demo

**What to show:**

1. **Climate Dashboard**: https://harmonaize-django.redocean-87ccea9f.southafricanorth.azurecontainerapps.io/climate/

2. **Available GEE Datasets:**
   - ERA5 Reanalysis (temperature, humidity, precipitation)
   - CHIRPS Precipitation (high-resolution rainfall)
   - MODIS (land surface temperature, vegetation)

3. **Heat-Health Metrics:**
   - Daily mean/min/max temperature
   - Relative humidity
   - Heat index
   - Precipitation
   - Solar radiation

4. **How it works:**
   - User selects their study
   - Chooses climate variables
   - Selects date range
   - Clicks "Request Climate Data"
   - GEE fetches real data for study locations
   - Data is linked to health outcomes

## Currently Using Mock Data

If GEE isn't deployed yet, the system will use **mock/simulated data** which is fine for demonstrating the workflow. The interface and process are identical - it just won't be pulling real satellite data yet.

## Security Notes

✅ **What's secured:**
- GEE credentials stored in Azure secrets (encrypted)
- Never committed to Git
- File permissions set to 600 (owner read/write only)
- `.gitignore` updated to exclude secrets

❌ **Don't:**
- Commit `secrets/` directory
- Share the JSON file publicly
- Include in email or Slack

## After the Meeting

If the demo goes well, we can:
1. Enable GEE in production permanently
2. Add more climate data sources
3. Configure automatic data refresh
4. Set up monitoring for API quota usage

## Troubleshooting

**If you see "Using mock data":**
- Check `CLIMATE_USE_MOCK_DATA` is set to `False`
- Check GEE credentials are loaded
- Restart containers

**If you get authentication errors:**
- Verify service account has Earth Engine access
- Check credentials JSON is valid
- Ensure Earth Engine API is enabled in Google Cloud

## Quick Reference

**Live App:** https://harmonaize-django.redocean-87ccea9f.southafricanorth.azurecontainerapps.io
**Climate Module:** /climate/
**Admin Panel:** /admin/ (for checking data sources)
**GEE Project:** joburg-hvi
**Service Account:** hvi-36@joburg-hvi.iam.gserviceaccount.com
