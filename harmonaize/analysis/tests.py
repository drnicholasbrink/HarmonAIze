from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from core.models import Project
from core.models import ProjectMembership

from .models import AnalysisScheduleConfiguration


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
