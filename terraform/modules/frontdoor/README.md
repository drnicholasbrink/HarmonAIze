# Front Door module — public edge + WAF for the web app

Azure **Front Door Standard** in front of the Container Apps `web` app, with a WAF policy. Enabling it
flips the Container Apps environment to **public** ingress and routes all public traffic through Front
Door. Because the origin (the Container Apps FQDN) is then publicly resolvable, **true lockdown — so no
one can bypass Front Door by hitting the origin directly — is enforced in Django** via an `X-Azure-FDID`
header check (see [Required Django change](#required-django-change)).

```
Browser ──HTTPS──► Front Door (WAF: rate-limit) ──HTTPS──► Container Apps web (public FQDN)
                                                                   │
   direct hit on the Container Apps FQDN ──► Django 403 (X-Azure-FDID mismatch)  ◄── the lockdown
```

- **SKU / cost:** Standard (~$35/mo base + usage). See [WAF coverage](#waf-coverage--premium-upgrade).
- **Provider:** needs `Microsoft.Cdn` registered (`az provider register --namespace Microsoft.Cdn`).
- Disabled by default — set `deploy_frontdoor = true` at the root to turn it on.

---

## Enable it (two-pass apply)

The app needs the Front Door ID (`X-Azure-FDID`) to lock the origin, but Front Door needs the web app's
FQDN as its origin — a chicken/egg. So apply in two passes (same pattern as the ACR image bring-up):

```powershell
# Pass 1 — create Front Door and make the env public (origin still open until pass 2)
#   terraform.tfvars:
#     deploy_frontdoor = true
terraform apply

# read the Front Door ID and public hostname
terraform output -raw frontdoor_id                 # e.g. 1234abcd-...-ef56
terraform output -raw frontdoor_endpoint_hostname  # harmonaize-prod-web-xxxx.z01.azurefd.net

# Pass 2 — inject the ID so Django locks the origin to Front Door
#   terraform.tfvars:
#     frontdoor_id = "<the GUID from above>"
terraform apply
# then roll a web revision so it picks up FRONTDOOR_ID:
az containerapp update -n harmonaize-prod-web -g harmonaize-prod-rg --revision-suffix "r$(Get-Date -UFormat %s)"
```

> ⚠️ **Bypass window.** Between pass 1 and pass 2 the origin is public **and** unlocked. Minimise it:
> ship the Django middleware (below) in the image **before** enabling Front Door, and do pass 2 promptly.
> The WAF protects traffic *through* Front Door immediately; the FDID check is what closes the direct-to-origin hole.

> ⚠️ **Destructive on an existing deployment.** Turning `deploy_frontdoor` on flips
> `internal_load_balancer_enabled` from `true`→`false`, which **forces recreation of the Container Apps
> environment and all of its apps**. Fine for a fresh deploy; plan a maintenance window otherwise.

---

## Required Django change

This is the lockdown — without it the public Container Apps FQDN is reachable directly, bypassing the
WAF. The app must (1) reject requests that didn't come through Front Door, and (2) trust the public host
for CSRF.

### 1. Add the middleware

Create `harmonaize/config/middleware.py`:

```python
from django.conf import settings
from django.http import HttpResponseForbidden


class FrontDoorIDMiddleware:
    """Reject requests that did not arrive through our Azure Front Door profile.

    Front Door injects the `X-Azure-FDID` header (our profile's unique ID) on every request it
    forwards, including health probes. We compare it to the expected FRONTDOOR_ID. When FRONTDOOR_ID
    is empty (e.g. local/dev, or before the two-pass apply completes) the check is disabled.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.expected = (getattr(settings, "FRONTDOOR_ID", "") or "").strip()

    def __call__(self, request):
        # Allow an unauthenticated health path through (Container Apps probes hit the origin directly).
        if self.expected and request.path != "/healthz/":
            if request.META.get("HTTP_X_AZURE_FDID", "") != self.expected:
                return HttpResponseForbidden("Direct origin access is not allowed.")
        return self.get_response(request)
```

### 2. Wire it up in `config/settings/production.py`

```python
from .base import MIDDLEWARE  # add to the existing imports from .base

# Front Door origin lockdown — value injected by Terraform (var.frontdoor_id → FRONTDOOR_ID).
FRONTDOOR_ID = env("FRONTDOOR_ID", default="")

# Run the check first so bypass attempts are rejected before any heavier processing.
MIDDLEWARE = ["config.middleware.FrontDoorIDMiddleware", *MIDDLEWARE]

# Registration/login POSTs over HTTPS need the public origin trusted for CSRF (Django 4+).
CSRF_TRUSTED_ORIGINS = env.list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    default=["https://harmonaize.org"],
)
```

### 3. Host headers / ALLOWED_HOSTS

The module forwards the **public** host to the origin (`origin_host_header`), so Django sees the real
domain and builds correct absolute URLs. Make sure the public host is in `ALLOWED_HOSTS`:

- **With a custom domain** (`frontdoor_custom_domain = "harmonaize.org"`): `allowed_hosts` already
  defaults to `harmonaize.org` — nothing to do.
- **Without a custom domain** (testing on `*.azurefd.net`): add that hostname to both `allowed_hosts`
  (tfvar) and `CSRF_TRUSTED_ORIGINS` after pass 1, e.g.
  `allowed_hosts = "harmonaize.org,harmonaize-prod-web-xxxx.z01.azurefd.net"`.

> The existing `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` already handles the
> TLS-terminated-at-the-edge case, so `SECURE_SSL_REDIRECT = True` won't loop.

---

## WAF coverage / Premium upgrade

On **Standard**, the WAF supports **custom rules only**. This module ships a per-IP **rate-limit** rule
(`var.rate_limit_threshold`, default 100/min) — useful against brute-force/credential-stuffing on
registration & login — but **not** the Microsoft-managed OWASP/bot rulesets (injection, XSS, etc.).

To get managed rules, upgrade to **Premium** — change the SKU on both the profile and the firewall
policy, and add a `managed_rule` block:

```hcl
# variables: sku_name = "Premium_AzureFrontDoor"

# in modules/frontdoor/main.tf, azurerm_cdn_frontdoor_firewall_policy.waf:
managed_rule {
  type    = "Microsoft_DefaultRuleSet"
  version = "2.1"
  action  = "Block"
}
managed_rule {
  type    = "Microsoft_BotManagerRuleSet"
  version = "1.0"
  action  = "Block"
}
```

(Premium also unlocks Private Link origins, so you could then keep the environment internal instead of
public — a larger change than this module.)

---

## Custom domain DNS

When `frontdoor_custom_domain` is set, Front Door issues a managed certificate but you must prove
ownership and route traffic:

1. `terraform output -raw custom_domain_validation_token`
2. Create a DNS **TXT** record: `_dnsauth.<your-domain>` = the token (validates domain ownership).
3. Create a DNS **CNAME**: `<your-domain>` → `frontdoor_endpoint_hostname` (the `*.azurefd.net` value).
   (For an apex/root domain, use your DNS provider's ALIAS/ANAME, or Azure DNS alias records.)
4. Wait for validation to flip to *Approved* and the managed cert to issue (can take 10–30+ min).

---

## Variables

| Name | Default | Purpose |
| :--- | :--- | :--- |
| `web_origin_host` | — | Container Apps web FQDN (origin TCP target). Wired from `module.compute.web_fqdn`. |
| `custom_domain_host` | `""` | Custom domain; blank = `*.azurefd.net` only. |
| `sku_name` | `Standard_AzureFrontDoor` | `Standard` (custom WAF rules) or `Premium` (managed rules + Private Link). |
| `waf_mode` | `Prevention` | `Prevention` (block) or `Detection` (log only). |
| `rate_limit_threshold` | `100` | Requests/min per client IP before the WAF blocks. |
| `health_probe_path` | `/` | Origin health-probe path; use an always-200 endpoint if `/` redirects. |

## Outputs

| Name | Purpose |
| :--- | :--- |
| `endpoint_hostname` | Public `*.azurefd.net` entry point. |
| `frontdoor_id` | The `X-Azure-FDID` value → set as `var.frontdoor_id` (pass 2). |
| `profile_id` | Front Door profile resource ID. |
| `custom_domain_validation_token` | DNS TXT validation token (null if no custom domain). |

## Verify

```powershell
# WAF + routing reachable via Front Door:
curl.exe -I "https://$(terraform output -raw frontdoor_endpoint_hostname)/"      # expect 200/redirect from the app

# Lockdown works (after pass 2 + middleware deployed): direct origin hit is rejected:
curl.exe -I "https://<container-app-fqdn>/"                                       # expect 403
```
