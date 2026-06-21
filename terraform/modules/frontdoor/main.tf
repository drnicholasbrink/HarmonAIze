# Azure Front Door **Standard** in front of the (now public) Container Apps web ingress.
#
# WAF on the Standard SKU supports **custom rules only** (rate limiting, IP/geo filtering). The
# Microsoft-managed OWASP/bot rulesets require the **Premium** SKU — see README.md for the one-line
# upgrade.
#
# The web origin (the Container Apps FQDN) is publicly resolvable. True lockdown — so nobody bypasses
# Front Door by hitting the Container Apps FQDN directly — is enforced in Django via an X-Azure-FDID
# header check. The app must read FRONTDOOR_ID (this module's `frontdoor_id` output). See README.md
# §"Required Django change".

locals {
  # Firewall-policy names must be alphanumeric and start with a letter (no hyphens).
  waf_name = substr(replace("${var.prefix}${var.environment}waf", "-", ""), 0, 128)

  # Forward the *public* host to the origin so Django builds correct absolute URLs (emails, redirects)
  # and ALLOWED_HOSTS / CSRF_TRUSTED_ORIGINS line up. Falls back to the generated Front Door hostname
  # when no custom domain is configured.
  public_host = var.custom_domain_host != "" ? var.custom_domain_host : azurerm_cdn_frontdoor_endpoint.ep.host_name
}

resource "azurerm_cdn_frontdoor_profile" "fd" {
  name                = "${var.prefix}-${var.environment}-afd"
  resource_group_name = var.resource_group_name
  sku_name            = var.sku_name
  tags                = var.tags
}

resource "azurerm_cdn_frontdoor_endpoint" "ep" {
  name                     = "${var.prefix}-${var.environment}-web"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.fd.id
  tags                     = var.tags
}

resource "azurerm_cdn_frontdoor_origin_group" "og" {
  name                     = "web-origin-group"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.fd.id
  session_affinity_enabled = false

  load_balancing {
    sample_size                 = 4
    successful_samples_required = 3
  }

  # Front Door always injects X-Azure-FDID on the probe too, so the probe passes the Django check.
  health_probe {
    path                = var.health_probe_path
    protocol            = "Https"
    request_type        = "HEAD"
    interval_in_seconds = 60
  }
}

resource "azurerm_cdn_frontdoor_origin" "web" {
  name                          = "web-origin"
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.og.id
  enabled                       = true

  # TCP target = the Container Apps ingress FQDN; its TLS cert is validated by SNI on host_name.
  host_name  = var.web_origin_host
  http_port  = 80
  https_port = 443
  # Host header MUST be the Container Apps FQDN: ACA ingress routes by Host and returns 404 for any
  # other value, so forwarding the public host here breaks routing. The app should set
  # USE_X_FORWARDED_HOST=True to recover the public hostname from the X-Forwarded-Host header.
  origin_host_header             = var.web_origin_host
  certificate_name_check_enabled = true
  priority                       = 1
  weight                         = 1000
}

resource "azurerm_cdn_frontdoor_custom_domain" "cd" {
  count                    = var.custom_domain_host != "" ? 1 : 0
  name                     = "${var.prefix}-${var.environment}-cd"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.fd.id
  host_name                = var.custom_domain_host

  tls {
    certificate_type = "ManagedCertificate"
  }
}

resource "azurerm_cdn_frontdoor_route" "route" {
  name                          = "web-route"
  cdn_frontdoor_endpoint_id     = azurerm_cdn_frontdoor_endpoint.ep.id
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.og.id
  cdn_frontdoor_origin_ids      = [azurerm_cdn_frontdoor_origin.web.id]

  supported_protocols    = ["Http", "Https"]
  patterns_to_match      = ["/*"]
  forwarding_protocol    = "HttpsOnly"
  https_redirect_enabled = true
  link_to_default_domain = true

  cdn_frontdoor_custom_domain_ids = var.custom_domain_host != "" ? [azurerm_cdn_frontdoor_custom_domain.cd[0].id] : []
}

resource "azurerm_cdn_frontdoor_firewall_policy" "waf" {
  name                = local.waf_name
  resource_group_name = var.resource_group_name
  sku_name            = var.sku_name
  enabled             = true
  mode                = var.waf_mode
  tags                = var.tags

  # Per-IP rate limit to blunt brute-force / credential-stuffing on registration & login.
  # Matches all requests (every RequestUri contains "/") and counts per client IP.
  custom_rule {
    name                           = "RateLimitPerIp"
    enabled                        = true
    priority                       = 1
    type                           = "RateLimitRule"
    action                         = "Block"
    rate_limit_duration_in_minutes = 1
    rate_limit_threshold           = var.rate_limit_threshold

    match_condition {
      match_variable = "RequestUri"
      operator       = "Contains"
      match_values   = ["/"]
    }
  }
}

resource "azurerm_cdn_frontdoor_security_policy" "sec" {
  name                     = "${var.prefix}-${var.environment}-secpol"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.fd.id

  security_policies {
    firewall {
      cdn_frontdoor_firewall_policy_id = azurerm_cdn_frontdoor_firewall_policy.waf.id

      association {
        patterns_to_match = ["/*"]

        domain {
          cdn_frontdoor_domain_id = azurerm_cdn_frontdoor_endpoint.ep.id
        }

        dynamic "domain" {
          for_each = var.custom_domain_host != "" ? [1] : []
          content {
            cdn_frontdoor_domain_id = azurerm_cdn_frontdoor_custom_domain.cd[0].id
          }
        }
      }
    }
  }
}
