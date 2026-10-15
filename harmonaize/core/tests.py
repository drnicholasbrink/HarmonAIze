from importlib import reload
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import SimpleNamespace

import pandas as pd
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from django.test import SimpleTestCase
from django.test import override_settings
from django.urls import clear_url_caches
from django.urls import resolve

from core.utils import infer_variables_from_dataframe


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


class VariableInferenceTests(SimpleTestCase):
	def test_infer_variables_from_dataframe_uses_column_name_heuristics(self):
		df = pd.DataFrame(
			{
				"patient_id": ["P1", "P2"],
				"age_years": [34, 51],
				"visit_date": ["2025-01-01", "2025-01-02"],
			}
		)

		variables = infer_variables_from_dataframe(df, source_label="demo.csv")
		lookup = {item["variable_name"]: item for item in variables}

		self.assertEqual(lookup["patient_id"]["variable_name"], "patient_id")
		self.assertEqual(lookup["patient_id"]["variable_type"], "string")
		self.assertEqual(lookup["age_years"]["display_name"], "Age Years")
		self.assertEqual(lookup["age_years"]["variable_type"], "int")
		self.assertEqual(lookup["age_years"]["unit"], "years")
		self.assertNotIn("Observed examples", lookup["age_years"]["description"])
		self.assertNotIn("Inferred", lookup["age_years"]["description"])
		self.assertEqual(lookup["visit_date"]["variable_type"], "datetime")

	def test_infer_variables_from_dataframe_uses_protocol_text_when_available(self):
		with NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
			handle.write("Systolic blood pressure (SBP) was measured in mmHg at enrolment.")
			protocol_path = handle.name

		class _EmptyDocuments:
			def all(self):
				return []

		study = SimpleNamespace(
			protocol_file=SimpleNamespace(path=protocol_path, name=Path(protocol_path).name),
			documents=_EmptyDocuments(),
			description="",
		)
		df = pd.DataFrame({"sbp": [120, 130]})

		try:
			variables = infer_variables_from_dataframe(df, study=study, source_label="vitals.csv")
		finally:
			Path(protocol_path).unlink(missing_ok=True)

		self.assertEqual(variables[0]["variable_name"], "sbp")
		self.assertIn("Systolic blood pressure", variables[0]["description"])
		self.assertEqual(variables[0]["unit"], "mmHg")
		self.assertNotIn("source:", variables[0]["description"].lower())
