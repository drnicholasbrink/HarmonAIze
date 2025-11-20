# CI/CD and Cost Management Setup

## 🚀 GitHub Actions CI/CD Pipeline

### What It Does:
- Automatically builds and deploys your app when you push to `climate-module-clean` or `main` branches
- Builds Docker image in Azure Container Registry
- Deploys new revision to Azure Container Apps
- Verifies deployment health

### Setup Instructions:

#### 1. Create Azure Service Principal

Run this command in your terminal:

```bash
az ad sp create-for-rbac \
  --name "harmonaize-github-actions" \
  --role contributor \
  --scopes /subscriptions/$(az account show --query id -o tsv)/resourceGroups/rg_harmonaize \
  --sdk-auth
```

This will output JSON like:
```json
{
  "clientId": "...",
  "clientSecret": "...",
  "subscriptionId": "...",
  "tenantId": "...",
  ...
}
```

#### 2. Add Secret to GitHub

1. Go to your GitHub repo: https://github.com/Logic06183/HarmonAIze
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Name: `AZURE_CREDENTIALS`
5. Value: Paste the entire JSON output from step 1
6. Click **Add secret**

#### 3. Test the Pipeline

Push a commit to `climate-module-clean` branch:
```bash
git commit --allow-empty -m "test: Trigger CI/CD pipeline"
git push origin climate-module-clean
```

Watch the deployment at: https://github.com/Logic06183/HarmonAIze/actions

## 💰 Cost Controls

### Current Optimizations (Already Active):

✅ **Scale to Zero** - minReplicas: 0
  - App scales down when no traffic
  - **Estimated savings**: ~70% vs always-on

✅ **Small Resources** - 0.5 CPU, 1GB RAM
  - Minimal resource allocation
  - **Cost**: ~$0.02/hour when active

✅ **Max Replicas Limited** - maxReplicas: 2
  - Prevents runaway scaling
  - **Maximum cost**: ~$0.04/hour peak

### Recommended Cost Monitoring:

#### 1. Set Up Budget Alerts

```bash
# Create a $50/month budget alert
az consumption budget create \
  --budget-name "harmonaize-monthly-budget" \
  --amount 50 \
  --time-grain Monthly \
  --start-date $(date +%Y-%m-01) \
  --end-date 2026-12-31 \
  --resource-group rg_harmonaize \
  --notifications threshold1="percentage=80.0,contact-emails=YOUR_EMAIL@example.com" \
  --notifications threshold2="percentage=100.0,contact-emails=YOUR_EMAIL@example.com"
```

Replace `YOUR_EMAIL@example.com` with your actual email.

#### 2. Monitor Costs Daily

Check costs in Azure Portal:
1. Go to: **Cost Management + Billing**
2. Select: **Cost analysis**
3. Filter by: **Resource group = rg_harmonaize**

Or use CLI:
```bash
# Check current month costs
az consumption usage list \
  --start-date $(date +%Y-%m-01) \
  --end-date $(date +%Y-%m-%d)
```

### Additional Cost Optimization Options:

#### Option 1: Reduce Max Replicas (Lower peak costs)
```bash
az containerapp update \
  --name harmonaize-django \
  --resource-group rg_harmonaize \
  --max-replicas 1  # Down from 2
```
**Impact**: Single instance only, may slow under high load

#### Option 2: Set Auto-Shutdown Schedule (Demo environments)
If only needed during work hours:
```bash
# Scale down at night (example)
az containerapp update \
  --name harmonaize-django \
  --resource-group rg_harmonaize \
  --min-replicas 0 \
  --max-replicas 0  # Completely off
```
**Note**: Requires manual restart or automation

#### Option 3: Use Azure Cost Alerts
Azure can automatically email you when costs exceed thresholds:
- Portal → **Cost Management** → **Budgets** → **Create Budget**
- Set monthly limit (e.g., $50)
- Add email notification at 80% and 100%

### Estimated Monthly Costs:

**Current Setup (Light usage):**
- Container App (scale to zero): $5-15/month
- PostgreSQL database: $25-35/month
- Container Registry: $5/month
- Redis (if used): $10/month
- **Total**: ~$45-65/month

**With Moderate Traffic:**
- Container App (frequent use): $15-30/month
- Database: $30-40/month
- Registry: $5/month
- Redis: $10/month
- **Total**: ~$60-85/month

### Cost Alerts Already Configured:

The current setup already has:
- ✅ Consumption-based pricing (pay per second)
- ✅ Scale to zero (no cost when idle)
- ✅ Resource limits prevent runaway costs
- ✅ South Africa North region (lower cost than US/EU)

### Monitoring Dashboard

View real-time costs:
```bash
# Get current month spending
az consumption usage list \
  --start-date $(date -d "-30 days" +%Y-%m-%d) \
  --end-date $(date +%Y-%m-%d) \
  --query "[].{Date:usageStart, Cost:pretaxCost.amount, Service:meterName}" \
  --output table
```

## 🔧 Maintenance

### Manual Deployment (if CI/CD not working)

```bash
# Build image
az acr build \
  --registry harmonaizeacr \
  --image harmonaize:latest \
  -f compose/production/django/Dockerfile .

# Deploy
az containerapp update \
  --name harmonaize-django \
  --resource-group rg_harmonaize \
  --image harmonaizeacr.azurecr.io/harmonaize:latest
```

### Check Application Health

```bash
# Check revision
az containerapp revision list \
  --name harmonaize-django \
  --resource-group rg_harmonaize \
  --query "[0].{Name:name, Health:properties.healthState, Replicas:properties.replicas}" \
  -o table

# View logs
az containerapp logs show \
  --name harmonaize-django \
  --resource-group rg_harmonaize \
  --tail 50
```

## 📊 Performance Optimization

If the app is slow, consider:

1. **Increase resources** (costs more):
   ```bash
   az containerapp update \
     --name harmonaize-django \
     --resource-group rg_harmonaize \
     --cpu 1.0 \
     --memory 2Gi
   ```

2. **Add caching** - Redis already configured

3. **Database optimization** - Enable connection pooling

## 🆘 Troubleshooting

### CI/CD Pipeline Fails

1. Check GitHub Actions logs
2. Verify Azure credentials secret
3. Ensure service principal has permissions

### High Costs

1. Check **Cost analysis** in Azure Portal
2. Look for:
   - High replica count (scale down max replicas)
   - Database oversized (downgrade tier)
   - Unused resources (delete if not needed)

### App Won't Start

1. Check container logs:
   ```bash
   az containerapp logs show --name harmonaize-django --resource-group rg_harmonaize --tail 100
   ```

2. Check revision health:
   ```bash
   az containerapp revision show \
     --name harmonaize-django \
     --resource-group rg_harmonaize \
     --revision <revision-name>
   ```
