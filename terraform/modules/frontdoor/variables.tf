variable "prefix" {
  type        = string
  description = "Prefix for resources"
}

variable "environment" {
  type        = string
  description = "Environment name"
}

variable "resource_group_name" {
  type        = string
  description = "Name of the resource group"
}

variable "tags" {
  type        = map(string)
  description = "Resource tags"
}

variable "web_origin_host" {
  type        = string
  description = "Public FQDN of the Container Apps web ingress (the Front Door origin TCP target)."
}

variable "custom_domain_host" {
  type        = string
  default     = ""
  description = "Optional custom domain (e.g. harmonaize.org). Blank = serve only on the generated *.azurefd.net hostname."
}

variable "sku_name" {
  type        = string
  default     = "Standard_AzureFrontDoor"
  description = "Front Door SKU. Standard = custom WAF rules only; Premium_AzureFrontDoor adds managed OWASP/bot rulesets (see README)."
}

variable "waf_mode" {
  type        = string
  default     = "Prevention"
  description = "WAF policy mode: Prevention (block) or Detection (log only)."
}

variable "rate_limit_threshold" {
  type        = number
  default     = 100
  description = "Requests per minute per client IP before the WAF rate-limit rule blocks."
}

variable "health_probe_path" {
  type        = string
  default     = "/"
  description = "Origin health-probe path. Use a lightweight always-200 endpoint if '/' redirects or requires auth."
}
