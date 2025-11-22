# Setting Up Real Email with SendGrid

Currently, verification and password reset emails go to console logs (for testing). To send real emails:

## Option 1: SendGrid (Recommended - Already Configured)

### 1. Create SendGrid Account

1. Go to: https://signup.sendgrid.com/
2. Sign up for free tier (100 emails/day free)
3. Verify your email and complete setup

### 2. Create API Key

1. In SendGrid dashboard: **Settings** → **API Keys**
2. Click **Create API Key**
3. Name: `harmonaize-production`
4. Permissions: **Full Access**
5. Click **Create & View**
6. **Copy the API key** (you won't see it again!)

### 3. Verify Sender Identity

1. Go to: **Settings** → **Sender Authentication**
2. Click **Verify a Single Sender**
3. Fill in your details:
   - **From Name**: HarmonAIze
   - **From Email**: noreply@harmonaize.org (or your domain)
4. Check your email and verify

### 4. Add API Key to Azure

```bash
# Add SendGrid API key as environment variable
az containerapp update \
  --name harmonaize-django \
  --resource-group rg_harmonaize \
  --set-env-vars "SENDGRID_API_KEY=YOUR_SENDGRID_API_KEY_HERE"
```

### 5. Switch to Production Settings (Optional)

If you want to use production settings with real email:

```bash
az containerapp update \
  --name harmonaize-django \
  --resource-group rg_harmonaize \
  --set-env-vars "DJANGO_SETTINGS_MODULE=config.settings.production"
```

**Note**: Production settings require additional env vars:
- `SENTRY_DSN` (error tracking - optional)
- `DJANGO_AZURE_ACCOUNT_KEY` (Azure blob storage - optional)
- `DJANGO_ADMIN_URL` (custom admin URL - optional)

### 6. Test Email

After deploying, test by:
1. Sign up with a real email address
2. Check your actual inbox for verification email
3. Click the link to verify

### 7. Monitor SendGrid

- Dashboard: https://app.sendgrid.com/
- View sent emails, delivery rates, bounces
- Free tier: 100 emails/day

---

## Option 2: Custom SMTP (Alternative)

If you want to use your own email server:

### 1. Update Production Settings

Edit `config/settings/production.py`:

```python
# Replace the SendGrid section with:
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = env('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_TLS = True
```

### 2. Add SMTP Credentials to Azure

```bash
az containerapp update \
  --name harmonaize-django \
  --resource-group rg_harmonaize \
  --set-env-vars \
    "EMAIL_HOST=smtp.gmail.com" \
    "EMAIL_PORT=587" \
    "EMAIL_HOST_USER=your-email@gmail.com" \
    "EMAIL_HOST_PASSWORD=your-app-specific-password"
```

**For Gmail:**
- You need an "App Password" (not your regular password)
- Go to: Google Account → Security → 2-Step Verification → App passwords

---

## Current Setup (Console Email)

Right now you're using:
```python
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
```

**Pros:**
- ✅ No setup required
- ✅ Works immediately
- ✅ Great for development/testing
- ✅ Free

**Cons:**
- ❌ Emails don't actually send
- ❌ Must check container logs for verification links
- ❌ Not suitable for production with real users

---

## Making Current User Admin (Without Email)

Since you already have an account, I've created a management command to make you admin:

### After Next Deployment:

The container will have a new command: `make_admin`

To make yourself admin, someone with Azure access needs to run:

```bash
az containerapp exec \
  --name harmonaize-django \
  --resource-group rg_harmonaize \
  --command "python manage.py make_admin craig.parker@wtisphr.org"
```

Or you can add this to the startup command temporarily.

### Alternative: Direct Database Update

If exec doesn't work, you can connect to PostgreSQL directly:

```bash
# Connect to database
PGPASSWORD='HarmOnAIze2024db' psql \
  -h harmonaize-db-za.postgres.database.azure.com \
  -U harmonaize_admin \
  -d harmonaize \
  -c "UPDATE users_user SET is_staff=true, is_superuser=true WHERE email='craig.parker@wtisphr.org';"
```

Then access admin at:
```
https://harmonaize-django.redocean-87ccea9f.southafricanorth.azurecontainerapps.io/admin/
```

---

## Cost Comparison

### SendGrid Free Tier
- **Cost**: $0/month
- **Limit**: 100 emails/day
- **Good for**: Small apps, testing, MVPs

### SendGrid Essentials
- **Cost**: $15/month
- **Limit**: 50,000 emails/month
- **Good for**: Production apps

### Custom SMTP (Gmail)
- **Cost**: $0 (personal) or $6/user/month (Google Workspace)
- **Limit**: 500/day (personal), 2,000/day (Workspace)
- **Good for**: Small apps with existing email

---

## Recommended Approach for Your Meeting

**Keep console email for now:**
- ✅ Works immediately
- ✅ Easy to demo
- ✅ Just check logs for verification links

**After the meeting, set up SendGrid:**
- Takes 10 minutes
- Free tier is plenty for testing
- Professional-looking emails

---

## Troubleshooting

### Emails Not Sending (SendGrid)

1. Check API key is set:
   ```bash
   az containerapp show --name harmonaize-django --resource-group rg_harmonaize \
     --query "properties.template.containers[0].env[?name=='SENDGRID_API_KEY']"
   ```

2. Check SendGrid dashboard for errors

3. Verify sender email is authenticated

### Container Logs

Always check logs when troubleshooting:
```bash
az containerapp logs show --name harmonaize-django --resource-group rg_harmonaize --tail 100
```
