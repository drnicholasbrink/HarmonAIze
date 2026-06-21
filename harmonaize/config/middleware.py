from django.conf import settings
from django.http import HttpResponseForbidden


class FrontDoorIDMiddleware:
    """Reject requests that did not arrive through our Azure Front Door profile.

    Front Door injects the ``X-Azure-FDID`` header (our profile's unique ID) on every request it
    forwards, including health probes. We compare it to the expected ``FRONTDOOR_ID``. When
    ``FRONTDOOR_ID`` is empty (e.g. local/dev, or before the two-pass apply completes) the check is
    disabled, so the app behaves normally until the origin lockdown is switched on.
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
