#!/bin/bash
# Deploy Google Earth Engine credentials to Azure Container App
# This script adds GEE credentials as an Azure secret and enables the GEE API

set -e

echo "🌍 Deploying Google Earth Engine to HarmonAIze Azure Container App"
echo ""

# Check if credentials file exists
CREDS_FILE="harmonaize/secrets/gee-credentials.json"
if [ ! -f "$CREDS_FILE" ]; then
    echo "❌ Error: GEE credentials file not found at $CREDS_FILE"
    exit 1
fi

echo "✅ Found GEE credentials file"
echo ""

# Resource details
RESOURCE_GROUP="rg_harmonaize"
CONTAINER_APP="harmonaize-django"

echo "📦 Resource Group: $RESOURCE_GROUP"
echo "🐳 Container App: $CONTAINER_APP"
echo ""

# Read the JSON file and encode it for Azure
GEE_CREDENTIALS=$(cat "$CREDS_FILE" | jq -c .)

echo "Step 1: Adding GEE credentials as secret to Container App..."
echo ""

# Note: This requires Azure CLI with proper permissions
# The credentials will be stored as a secret in Azure

# Update container app with GEE credentials
az containerapp secret set \
  --name "$CONTAINER_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --secrets gee-credentials="$GEE_CREDENTIALS" \
  --output none

echo "✅ GEE credentials added as secret"
echo ""

echo "Step 2: Enabling GEE API in climate module..."
echo ""

# Update environment variables to enable GEE
az containerapp update \
  --name "$CONTAINER_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --set-env-vars \
    "CLIMATE_USE_MOCK_DATA=False" \
    "GEE_PROJECT_ID=joburg-hvi" \
  --replace-env-vars \
    "GOOGLE_APPLICATION_CREDENTIALS=secretref:gee-credentials" \
  --output none

echo "✅ GEE API enabled"
echo ""

echo "Step 3: Restarting container to apply changes..."
echo ""

# Trigger a new revision
az containerapp revision restart \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_APP" \
  --revision "$(az containerapp revision list --name $CONTAINER_APP --resource-group $RESOURCE_GROUP --query '[0].name' -o tsv)" \
  --output none

echo "✅ Container restarted"
echo ""

echo "🎉 Google Earth Engine is now configured!"
echo ""
echo "📍 Next steps:"
echo "1. Visit: https://harmonaize-django.redocean-87ccea9f.southafricanorth.azurecontainerapps.io/climate/"
echo "2. Configure a climate data request"
echo "3. Select GEE data sources (ERA5, CHIRPS, MODIS)"
echo "4. Real climate data will be fetched from Google Earth Engine"
echo ""
echo "🔍 Check logs:"
echo "   az containerapp logs show --name $CONTAINER_APP --resource-group $RESOURCE_GROUP --tail 50"
