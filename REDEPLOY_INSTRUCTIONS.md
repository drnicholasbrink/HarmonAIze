# HarmonAIze Azure Redeployment Instructions

**Last Updated:** 2025-11-20
**Change:** Disabled email verification for demo (commit: 3b61032)

## What Changed

Modified `harmonaize/config/settings/base.py`:
- Changed `ACCOUNT_EMAIL_VERIFICATION` from `"mandatory"` to `"none"`
- Users can now sign up without email verification
- **TODO:** Re-enable with proper email backend for production

## Quick Redeploy Options

### Option 1: Via Azure Portal (Easiest if you have permissions)

1. Go to: https://portal.azure.com
2. Navigate to: **Container Apps** → **harmonaize-django**
3. Click **"Revision management"** → **"Create new revision"**
4. This will pull the latest image and redeploy

### Option 2: Rebuild and Push (If someone set up CI/CD)

If there's a CI/CD pipeline:
1. Push to `climate-module-clean` branch (✅ Already done)
2. Wait for automatic build and deployment

### Option 3: Manual Docker Build and Push

**Prerequisites:** Someone with Azure Container Registry push permissions

```bash
# Login to Azure Container Registry
az acr login --name harmonaizeacr

# Build the Docker image
cd harmonaize
docker build -f compose/production/django/Dockerfile -t harmonaizeacr.azurecr.io/harmonaize:latest .

# Push to registry
docker push harmonaizeacr.azurecr.io/harmonaize:latest

# Update the container app (requires permissions)
az containerapp update \
  --name harmonaize-django \
  --resource-group rg_harmonaize \
  --image harmonaizeacr.azurecr.io/harmonaize:latest
```

### Option 4: Restart Existing Container (Quick Test)

Sometimes a simple restart picks up changes:
```bash
az containerapp revision restart \
  --resource-group rg_harmonaize \
  --name harmonaize-django \
  --revision harmonaize-django--0000012
```

## Verify Deployment

1. Visit: https://harmonaize-django.redocean-87ccea9f.southafricanorth.azurecontainerapps.io/accounts/signup/
2. Try signing up with test credentials
3. Should be able to sign up and log in **immediately without email verification**

## For Meeting Demo

**What to show:**
1. ✅ Live site: https://harmonaize-django.redocean-87ccea9f.southafricanorth.azurecontainerapps.io
2. ✅ Sign-up process (now working)
3. ✅ Climate module features
4. ✅ Azure deployment (Container Apps)

## Important Notes for Production

⚠️ **Current Setup:**
- Using `config.settings.local` (development mode)
- Email verification disabled
- DEBUG = True

🔧 **For Proper Production Deployment:**
1. Change `DJANGO_SETTINGS_MODULE` to `config.settings.production`
2. Configure SendGrid API key for emails
3. Re-enable `ACCOUNT_EMAIL_VERIFICATION = "mandatory"`
4. Set `DEBUG = False`
5. Configure proper `ALLOWED_HOSTS`

## Azure Resources

- **Resource Group:** rg_harmonaize
- **Container Registry:** harmonaizeacr
- **Container Apps:** harmonaize-django, harmonaize-celery, redis
- **Database:** harmonaize-db-za (PostgreSQL 15)
- **Live URL:** https://harmonaize-django.redocean-87ccea9f.southafricanorth.azurecontainerapps.io

## Who Can Help

If you don't have permissions to redeploy, contact whoever initially set up the Azure resources in `rg_harmonaize`.
