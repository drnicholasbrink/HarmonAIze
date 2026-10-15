from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import SimpleTestCase
from django.test import TestCase
from django.urls import reverse

from core.models import Project
from core.models import ProjectMembership

from .models import AnalysisScheduleConfiguration
from .services import AnalysisExportService


class AnalysisExportServiceTests(SimpleTestCase):
	def test_legacy_names_exclude_current_object_parent_directory(self):
		service = AnalysisExportService.__new__(AnalysisExportService)
		target_study = SimpleNamespace(name="RP1", id=1)

		legacy_names = service._legacy_object_names(
			target_study,
			"rp1-prem-birth-lag7d-long",
			current_object_name="rp1-prem-birth-lag7d-long/data.parquet",
		)

		self.assertNotIn("rp1-prem-birth-lag7d-long", legacy_names)
		self.assertEqual(
			legacy_names,
			[
				"rp1/rp1-prem-birth-lag7d-long/data.parquet",
				"rp1/rp1-prem-birth-lag7d-long",
			],
		)


class AnalysisAccessTests(TestCase):
	def setUp(self):
		self.user_model = get_user_model()
		self.user = self.user_model.objects.create_user(
			email="analysis-user@example.com",
			password="test-pass-123",
		)
		self.group, _ = Group.objects.get_or_create(name="analysis_admin")
		AnalysisScheduleConfiguration.objects.get_or_create(name="default")

	def test_dashboard_forbidden_without_group(self):
		self.client.force_login(self.user)
		response = self.client.get(reverse("analysis:dashboard"))
		self.assertEqual(response.status_code, 403)
		self.assertTemplateUsed(response, "analysis/access_denied.html")

	def test_dashboard_visible_with_group(self):
		self.user.groups.add(self.group)
		self.client.force_login(self.user)
		response = self.client.get(reverse("analysis:dashboard"))
		self.assertEqual(response.status_code, 200)

	def test_dashboard_visible_for_project_manager(self):
		project = Project.objects.create(
			name="Manager Project",
			description="",
			created_by=self.user,
		)
		ProjectMembership.objects.create(project=project, user=self.user, role="manager")

		self.client.force_login(self.user)
		response = self.client.get(reverse("analysis:dashboard"))
		self.assertEqual(response.status_code, 200)
