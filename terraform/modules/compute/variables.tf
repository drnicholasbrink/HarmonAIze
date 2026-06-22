variable "prefix" {
  type        = string
  description = "Prefix for resources"
}

variable "environment" {
  type        = string
  description = "Environment name"
}

variable "location" {
  type        = string
  description = "Azure region"
}

variable "resource_group_name" {
  type        = string
  description = "Name of the resource group"
}

variable "tags" {
  type        = map(string)
  description = "Resource tags"
}

variable "containerapps_subnet_id" {
  type        = string
  description = "ID of the Container Apps subnet"
}

variable "container_image" {
  type        = string
  description = "Container image URI"
}

variable "acr_sku" {
  type        = string
  description = "ACR SKU (Basic/Standard/Premium)"
}

variable "min_replicas" {
  type        = number
  description = "Minimum number of replicas"
}

variable "max_replicas" {
  type        = number
  description = "Maximum number of replicas"
}

variable "key_vault_id" {
  type        = string
  description = "ID of the Key Vault (for the AcrPull/Secrets User role assignments)"
}

variable "suffix" {
  type        = string
  description = "Random suffix for globally unique names"
}

variable "allowed_hosts" {
  type        = string
  description = "Comma-separated value for DJANGO_ALLOWED_HOSTS"
}

variable "analysis_enabled" {
  type        = string
  default     = "false"
  description = "Value for ANALYSIS_ENABLED. Keep 'false' until the Armadillo/DataSHIELD stack is deployed."
}

variable "storage_account_name" {
  type        = string
  description = "Storage account name (non-secret env value)"
}

# --- Redis coordinates for the KEDA scaler (non-secret) ---
variable "redis_hostname" {
  type        = string
  description = "Redis hostname (KEDA scaler address)"
}

variable "redis_ssl_port" {
  type        = number
  description = "Redis SSL port (KEDA scaler address)"
}

variable "celery_queue_name" {
  type        = string
  default     = "celery"
  description = "Name of the Redis list Celery uses as its default queue (KEDA listName)"
}

variable "worker_target_queue_length" {
  type        = number
  default     = 5
  description = "Target Celery queue length per worker replica before KEDA scales out"
}

# --- Key Vault secret IDs (all app secrets are sourced from Key Vault) ---
variable "django_secret_key_secret_id" {
  type        = string
  description = "KV secret ID for DJANGO_SECRET_KEY"
}

variable "database_url_secret_id" {
  type        = string
  description = "KV secret ID for DATABASE_URL"
}

variable "redis_url_secret_id" {
  type        = string
  description = "KV secret ID for REDIS_URL / CELERY_BROKER_URL"
}

variable "redis_password_secret_id" {
  type        = string
  description = "KV secret ID for the Redis access key (KEDA scaler auth)"
}

variable "storage_account_key_secret_id" {
  type        = string
  description = "KV secret ID for DJANGO_AZURE_ACCOUNT_KEY"
}

variable "django_admin_url_secret_id" {
  type        = string
  description = "KV secret ID for DJANGO_ADMIN_URL"
}

variable "sendgrid_api_key_secret_id" {
  type        = string
  description = "KV secret ID for SENDGRID_API_KEY"
}

variable "sentry_dsn_secret_id" {
  type        = string
  description = "KV secret ID for SENTRY_DSN"
}

variable "openai_api_key_secret_id" {
  type        = string
  description = "KV secret ID for OPENAI_API_KEY"
}

variable "openai_base_url_secret_id" {
  type        = string
  description = "KV secret ID for OPENAI_BASE_URL"
}

variable "openai_embedding_model_secret_id" {
  type        = string
  description = "KV secret ID for OPENAI_EMBEDDING_MODEL"
}

variable "openai_transformation_model_secret_id" {
  type        = string
  description = "KV secret ID for OPENAI_TRANSFORMATION_MODEL"
}

variable "google_geocoding_api_key_secret_id" {
  type        = string
  description = "KV secret ID for GOOGLE_GEOCODING_API_KEY"
}

variable "gemini_api_key_secret_id" {
  type        = string
  description = "KV secret ID for GEMINI_API_KEY"
}

variable "mapbox_access_token_secret_id" {
  type        = string
  description = "KV secret ID for MAPBOX_ACCESS_TOKEN"
}

variable "analysis_deidentification_salt_secret_id" {
  type        = string
  description = "KV secret ID for ANALYSIS_DEIDENTIFICATION_SALT"
}

variable "flower_password_secret_id" {
  type        = string
  description = "KV secret ID for FLOWER_PASSWORD"
}

variable "deploy_analysis_stack" {
  type        = bool
  default     = false
  description = "Whether the analysis stack is deployed"
}

variable "armadillo_admin_password_secret_id" {
  type        = string
  default     = null
  description = "KV secret ID for Armadillo admin password"
}

variable "keycloak_admin_password_secret_id" {
  type        = string
  default     = null
  description = "KV secret ID for Keycloak admin password"
}

# --- Public ingress / Front Door integration ---
variable "enable_external_ingress" {
  type        = bool
  default     = false
  description = "Expose the Container Apps environment via a PUBLIC load balancer (required when fronting with Front Door). Default false = internal/private VIP. NOTE: changing this on an existing environment forces its recreation."
}

variable "frontdoor_id" {
  type        = string
  default     = ""
  description = "Front Door ID (X-Azure-FDID). When set, injected as FRONTDOOR_ID so the app can reject traffic that did not arrive through Front Door. Blank disables the check."
}
