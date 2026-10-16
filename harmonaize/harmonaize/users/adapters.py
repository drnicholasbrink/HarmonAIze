from __future__ import annotations

import typing

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

if typing.TYPE_CHECKING:
    from allauth.socialaccount.models import SocialLogin
    from django.http import HttpRequest

    from harmonaize.users.models import User


def allowed_email_domains() -> set[str]:
    """Domains from settings plus those superusers added in the admin."""
    from harmonaize.users.models import AllowedEmailDomain

    domains = {
        domain.strip().lower().lstrip("@")
        for domain in getattr(settings, "ACCOUNT_ALLOWED_EMAIL_DOMAINS", [])
        if domain.strip()
    }
    domains.update(AllowedEmailDomain.objects.values_list("domain", flat=True))
    return domains


def is_email_domain_allowed(email: str) -> bool:
    """True when the email's domain, or a parent domain, is on the allow list."""
    domain = email.rsplit("@", 1)[-1].strip().lower() if "@" in email else ""
    if not domain:
        return False
    allowed = allowed_email_domains()
    return any(domain == item or domain.endswith(f".{item}") for item in allowed)


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True)

    def clean_email(self, email: str) -> str:
        email = super().clean_email(email)
        if not is_email_domain_allowed(email):
            raise ValidationError(
                _("Registration is limited to approved organisations. Contact an administrator to request access."),
            )
        return email


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(
        self,
        request: HttpRequest,
        sociallogin: SocialLogin,
    ) -> bool:
        if not getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True):
            return False
        return is_email_domain_allowed(sociallogin.user.email or "")

    def populate_user(
        self,
        request: HttpRequest,
        sociallogin: SocialLogin,
        data: dict[str, typing.Any],
    ) -> User:
        """
        Populates user information from social provider info.

        See: https://docs.allauth.org/en/latest/socialaccount/advanced.html#creating-and-populating-user-instances
        """
        user = super().populate_user(request, sociallogin, data)
        if not user.name:
            if name := data.get("name"):
                user.name = name
            elif first_name := data.get("first_name"):
                user.name = first_name
                if last_name := data.get("last_name"):
                    user.name += f" {last_name}"
        return user
