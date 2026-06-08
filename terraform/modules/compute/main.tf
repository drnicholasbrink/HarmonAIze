locals {
  acr_name = substr(replace("${var.prefix}${var.environment}acr${var.suffix}", "-", ""), 0, 50)

  # Every application secret is sourced from Key Vault via the app's managed identity.
  kv_secrets = [
    { name = "django-secret-key", id = var.django_secret_key_secret_id },
    { name = "database-url", id = var.database_url_secret_id },
    { name = "redis-url", id = var.redis_url_secret_id },
    { name = "storage-account-key", id = var.storage_account_key_secret_id },
    { name = "django-admin-url", id = var.django_admin_url_secret_id },
    { name = "sendgrid-api-key", id = var.sendgrid_api_key_secret_id },
    { name = "sentry-dsn", id = var.sentry_dsn_secret_id },
    { name = "openai-api-key", id = var.openai_api_key_secret_id },
    { name = "google-geocoding-api-key", id = var.google_geocoding_api_key_secret_id },
    { name = "gemini-api-key", id = var.gemini_api_key_secret_id },
    { name = "mapbox-access-token", id = var.mapbox_access_token_secret_id },
    { name = "analysis-deidentification-salt", id = var.analysis_deidentification_salt_secret_id },
  ]

  # The worker also needs the raw Redis key for the KEDA scaler's TriggerAuthentication.
  worker_secrets = concat(local.kv_secrets, [
    { name = "redis-password", id = var.redis_password_secret_id },
  ])

  plain_env = [
    { name = "DJANGO_SETTINGS_MODULE", value = "config.settings.production" },
    { name = "DJANGO_ALLOWED_HOSTS", value = var.allowed_hosts },
    { name = "DJANGO_AZURE_ACCOUNT_NAME", value = var.storage_account_name },
    { name = "DJANGO_AZURE_CONTAINER_NAME", value = "media" },
    { name = "OPENAI_BASE_URL", value = var.openai_base_url },
  ]

  secret_env = [
    { name = "DJANGO_SECRET_KEY", secret_name = "django-secret-key" },
    { name = "DATABASE_URL", secret_name = "database-url" },
    { name = "REDIS_URL", secret_name = "redis-url" },
    { name = "CELERY_BROKER_URL", secret_name = "redis-url" },
    { name = "DJANGO_AZURE_ACCOUNT_KEY", secret_name = "storage-account-key" },
    { name = "DJANGO_ADMIN_URL", secret_name = "django-admin-url" },
    { name = "SENDGRID_API_KEY", secret_name = "sendgrid-api-key" },
    { name = "SENTRY_DSN", secret_name = "sentry-dsn" },
    { name = "OPENAI_API_KEY", secret_name = "openai-api-key" },
    { name = "GOOGLE_GEOCODING_API_KEY", secret_name = "google-geocoding-api-key" },
    { name = "GEMINI_API_KEY", secret_name = "gemini-api-key" },
    { name = "MAPBOX_ACCESS_TOKEN", secret_name = "mapbox-access-token" },
    { name = "ANALYSIS_DEIDENTIFICATION_SALT", secret_name = "analysis-deidentification-salt" },
  ]
}

resource "azurerm_user_assigned_identity" "app" {
  name                = "${var.prefix}-${var.environment}-app-identity"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_container_registry" "acr" {
  name                = local.acr_name
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = var.acr_sku
  admin_enabled       = false
  tags                = var.tags
}

resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_role_assignment" "kv_secrets" {
  scope                = var.key_vault_id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_log_analytics_workspace" "law" {
  name                = "${var.prefix}-${var.environment}-law"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

resource "azurerm_container_app_environment" "env" {
  name                           = "${var.prefix}-${var.environment}-cae"
  location                       = var.location
  resource_group_name            = var.resource_group_name
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.law.id
  infrastructure_subnet_id       = var.containerapps_subnet_id
  internal_load_balancer_enabled = true
  tags                           = var.tags
}

# --- Web (HTTP ingress, autoscale on concurrency) ---
resource "azurerm_container_app" "web" {
  name                         = "${var.prefix}-${var.environment}-web"
  container_app_environment_id = azurerm_container_app_environment.env.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  depends_on = [azurerm_role_assignment.kv_secrets]

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.acr.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  dynamic "secret" {
    for_each = local.kv_secrets
    content {
      name                = secret.value.name
      identity            = azurerm_user_assigned_identity.app.id
      key_vault_secret_id = secret.value.id
    }
  }

  ingress {
    external_enabled = true
    target_port      = 5000

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    min_replicas = var.min_replicas > 0 ? var.min_replicas : 1
    max_replicas = var.max_replicas

    container {
      name   = "web"
      image  = var.container_image
      cpu    = 0.5
      memory = "1Gi"

      dynamic "env" {
        for_each = local.plain_env
        content {
          name  = env.value.name
          value = env.value.value
        }
      }

      dynamic "env" {
        for_each = local.secret_env
        content {
          name        = env.value.name
          secret_name = env.value.secret_name
        }
      }
    }

    http_scale_rule {
      name                = "http-scale"
      concurrent_requests = 50
    }
  }
}

# --- Celery worker (no ingress, KEDA Redis scaler, scale-to-zero) ---
resource "azurerm_container_app" "worker" {
  name                         = "${var.prefix}-${var.environment}-worker"
  container_app_environment_id = azurerm_container_app_environment.env.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  depends_on = [azurerm_role_assignment.kv_secrets]

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.acr.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  dynamic "secret" {
    for_each = local.worker_secrets
    content {
      name                = secret.value.name
      identity            = azurerm_user_assigned_identity.app.id
      key_vault_secret_id = secret.value.id
    }
  }

  template {
    min_replicas = 0
    max_replicas = var.max_replicas

    container {
      name    = "worker"
      image   = var.container_image
      cpu     = 0.5
      memory  = "1Gi"
      command = ["/start-celeryworker"]

      dynamic "env" {
        for_each = local.plain_env
        content {
          name  = env.value.name
          value = env.value.value
        }
      }

      dynamic "env" {
        for_each = local.secret_env
        content {
          name        = env.value.name
          secret_name = env.value.secret_name
        }
      }
    }

    # KEDA Redis-list scaler: scales the worker (incl. from zero) on Celery queue depth.
    custom_scale_rule {
      name             = "celery-redis-scaler"
      custom_rule_type = "redis"

      metadata = {
        address       = "${var.redis_hostname}:${var.redis_ssl_port}"
        listName      = var.celery_queue_name
        listLength    = tostring(var.worker_target_queue_length)
        enableTLS     = "true"
        databaseIndex = "0"
      }

      authentication {
        secret_name       = "redis-password"
        trigger_parameter = "password"
      }
    }
  }
}

# --- Celery beat (singleton scheduler) ---
resource "azurerm_container_app" "beat" {
  name                         = "${var.prefix}-${var.environment}-beat"
  container_app_environment_id = azurerm_container_app_environment.env.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  depends_on = [azurerm_role_assignment.kv_secrets]

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.acr.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  dynamic "secret" {
    for_each = local.kv_secrets
    content {
      name                = secret.value.name
      identity            = azurerm_user_assigned_identity.app.id
      key_vault_secret_id = secret.value.id
    }
  }

  template {
    min_replicas = 1
    max_replicas = 1

    container {
      name    = "beat"
      image   = var.container_image
      cpu     = 0.5
      memory  = "1Gi"
      command = ["/start-celerybeat"]

      dynamic "env" {
        for_each = local.plain_env
        content {
          name  = env.value.name
          value = env.value.value
        }
      }

      dynamic "env" {
        for_each = local.secret_env
        content {
          name        = env.value.name
          secret_name = env.value.secret_name
        }
      }
    }
  }
}

# --- Flower (internal-only monitoring UI) ---
resource "azurerm_container_app" "flower" {
  name                         = "${var.prefix}-${var.environment}-flower"
  container_app_environment_id = azurerm_container_app_environment.env.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  depends_on = [azurerm_role_assignment.kv_secrets]

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.acr.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  dynamic "secret" {
    for_each = local.kv_secrets
    content {
      name                = secret.value.name
      identity            = azurerm_user_assigned_identity.app.id
      key_vault_secret_id = secret.value.id
    }
  }

  ingress {
    external_enabled = false
    target_port      = 5555

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    min_replicas = 1
    max_replicas = 1

    container {
      name    = "flower"
      image   = var.container_image
      cpu     = 0.5
      memory  = "1Gi"
      command = ["/start-flower"]

      dynamic "env" {
        for_each = local.plain_env
        content {
          name  = env.value.name
          value = env.value.value
        }
      }

      dynamic "env" {
        for_each = local.secret_env
        content {
          name        = env.value.name
          secret_name = env.value.secret_name
        }
      }
    }
  }
}
