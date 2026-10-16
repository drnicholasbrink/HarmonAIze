import pytest
from django.core.exceptions import ValidationError

from harmonaize.users.adapters import AccountAdapter
from harmonaize.users.adapters import is_email_domain_allowed
from harmonaize.users.models import AllowedEmailDomain

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _allowed_domains(settings):
    settings.ACCOUNT_ALLOWED_EMAIL_DOMAINS = ["witsphr.org", "witsphr.co.za"]


class TestEmailDomainAllowList:
    def test_settings_domains_allowed(self):
        assert is_email_domain_allowed("a@witsphr.org")
        assert is_email_domain_allowed("a@WITSPHR.CO.ZA")
        assert is_email_domain_allowed("a@students.witsphr.org")

    def test_other_domains_rejected(self):
        assert not is_email_domain_allowed("a@gmail.com")
        assert not is_email_domain_allowed("a@evilwitsphr.org")
        assert not is_email_domain_allowed("a@witsphr.org.evil.com")
        assert not is_email_domain_allowed("not-an-email")

    def test_admin_added_domain_allowed(self):
        AllowedEmailDomain.objects.create(domain="@Partner.ac.za ")
        assert is_email_domain_allowed("a@partner.ac.za")

    def test_adapter_rejects_disallowed_signup_email(self):
        with pytest.raises(ValidationError):
            AccountAdapter().clean_email("someone@gmail.com")
        assert AccountAdapter().clean_email("someone@witsphr.org") == "someone@witsphr.org"
