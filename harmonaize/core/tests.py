from importlib import reload
from types import SimpleNamespace

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from django.test import SimpleTestCase
from django.test import override_settings
from django.urls import clear_url_caches
from django.urls import resolve


class AnalysisAvailabilityTests(SimpleTestCase):
	def setUp(self):
		self.factory = RequestFactory()

	def _reload_root_urls(self):
		from config import urls as root_urls

		clear_url_caches()
		return reload(root_urls)

	def _build_authenticated_request(self, path: str):
		request = self.factory.get(path)
		SessionMiddleware(lambda req: None).process_request(request)
		setattr(request, "_messages", FallbackStorage(request))
		request.user = SimpleNamespace(is_authenticated=True, pk=1)
		return request

	def test_analysis_dashboard_shows_unavailable_page_when_disabled(self):
		with override_settings(ANALYSIS_ENABLED=False):
			self._reload_root_urls()
			request = self._build_authenticated_request("/analysis/")
			response = resolve("/analysis/").func(request)

		self._reload_root_urls()

		self.assertEqual(response.status_code, 200)
		self.assertIn("pages/analysis_unavailable.html", response.template_name)

	def test_analysis_dashboard_requires_login_when_disabled(self):
		with override_settings(ANALYSIS_ENABLED=False):
			self._reload_root_urls()
			response = self.client.get("/analysis/")

		self._reload_root_urls()

		self.assertEqual(response.status_code, 302)
		self.assertIn("/accounts/login/", response.url)
