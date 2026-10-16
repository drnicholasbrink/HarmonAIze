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


# ---------------------------------------------------------------------------
# Security regression tests (GHSA-fgxj-f48g-q364, GHSA-3cm2-fwx3-gc8h,
# GHSA-69hh-h399-r874)
# ---------------------------------------------------------------------------
import json
import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Attribute
from core.models import Project
from core.models import ProjectInvitation
from core.models import ProjectMembership
from core.models import Study

_TEMP_MEDIA = tempfile.mkdtemp(prefix="hz-core-tests-")


def _make_user(email):
	return get_user_model().objects.create_user(email=email, password="pw-12345!")


def _make_project(owner, name="Proj"):
	project = Project.objects.create(name=name, created_by=owner)
	ProjectMembership.objects.create(project=project, user=owner, role="owner")
	return project


def _make_study(project, user, name="Study"):
	return Study.objects.create(
		name=name,
		project=project,
		created_by=user,
		study_type="cohort",
	)


class ProjectMembershipSecurityTests(TestCase):
	def setUp(self):
		self.owner = _make_user("owner@example.com")
		self.manager = _make_user("manager@example.com")
		self.member = _make_user("member@example.com")
		self.project = _make_project(self.owner)
		ProjectMembership.objects.create(project=self.project, user=self.manager, role="manager")
		self.member_ms = ProjectMembership.objects.create(
			project=self.project, user=self.member, role="member",
		)

	def test_remove_member_get_not_allowed(self):
		self.client.force_login(self.owner)
		url = reverse("core:remove_member", args=[self.project.pk, self.member_ms.pk])
		response = self.client.get(url)
		self.assertEqual(response.status_code, 405)
		self.assertTrue(ProjectMembership.objects.filter(pk=self.member_ms.pk).exists())

	def test_remove_member_post_works(self):
		self.client.force_login(self.owner)
		url = reverse("core:remove_member", args=[self.project.pk, self.member_ms.pk])
		response = self.client.post(url)
		self.assertEqual(response.status_code, 302)
		self.assertFalse(ProjectMembership.objects.filter(pk=self.member_ms.pk).exists())

	def test_members_page_uses_post_form_for_remove(self):
		self.client.force_login(self.owner)
		response = self.client.get(reverse("core:project_members", args=[self.project.pk]))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'method="post"')
		self.assertNotContains(response, '<a href="%s"' % reverse(
			"core:remove_member", args=[self.project.pk, self.member_ms.pk],
		))

	def test_manager_cannot_invite_owner(self):
		self.client.force_login(self.manager)
		response = self.client.post(
			reverse("core:invite_member", args=[self.project.pk]),
			{"email": "new@example.com", "role": "owner"},
		)
		self.assertEqual(response.status_code, 302)
		self.assertFalse(ProjectInvitation.objects.filter(email="new@example.com").exists())

	def test_manager_form_does_not_offer_owner(self):
		self.client.force_login(self.manager)
		response = self.client.get(reverse("core:project_members", args=[self.project.pk]))
		roles = [value for value, _ in response.context["form"].fields["role"].choices]
		self.assertEqual(roles, ["manager", "member"])

	def test_manager_can_invite_member(self):
		self.client.force_login(self.manager)
		self.client.post(
			reverse("core:invite_member", args=[self.project.pk]),
			{"email": "new@example.com", "role": "member"},
		)
		self.assertTrue(ProjectInvitation.objects.filter(email="new@example.com", role="member").exists())

	def test_member_cannot_invite(self):
		self.client.force_login(self.member)
		self.client.post(
			reverse("core:invite_member", args=[self.project.pk]),
			{"email": "new@example.com", "role": "member"},
		)
		self.assertFalse(ProjectInvitation.objects.exists())

	def test_inviting_existing_member_does_not_500(self):
		self.client.force_login(self.owner)
		response = self.client.post(
			reverse("core:invite_member", args=[self.project.pk]),
			{"email": "MEMBER@example.com", "role": "member"},
		)
		self.assertEqual(response.status_code, 302)
		self.assertFalse(ProjectInvitation.objects.exists())

	def _invite(self, email, role="member", inviter=None, sent_at=None):
		invitation = ProjectInvitation.objects.create(
			email=email,
			project=self.project,
			role=role,
			invited_by=inviter or self.owner,
			key=f"key-{email}-{role}",
		)
		if sent_at is not None:
			ProjectInvitation.objects.filter(pk=invitation.pk).update(sent_at=sent_at)
			invitation.refresh_from_db()
		return invitation

	def test_accept_invite_get_does_not_add_membership(self):
		invitee = _make_user("invitee@example.com")
		invitation = self._invite("invitee@example.com")
		self.client.force_login(invitee)
		response = self.client.get(reverse("core:accept_invite", args=[invitation.key]))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "csrfmiddlewaretoken")
		self.assertFalse(ProjectMembership.objects.filter(project=self.project, user=invitee).exists())
		invitation.refresh_from_db()
		self.assertEqual(invitation.status, "pending")

	def test_accept_invite_post_adds_membership(self):
		invitee = _make_user("invitee@example.com")
		invitation = self._invite("invitee@example.com")
		self.client.force_login(invitee)
		response = self.client.post(reverse("core:accept_invite", args=[invitation.key]))
		self.assertEqual(response.status_code, 302)
		self.assertTrue(
			ProjectMembership.objects.filter(project=self.project, user=invitee, role="member").exists(),
		)
		invitation.refresh_from_db()
		self.assertEqual(invitation.status, "accepted")

	def test_expired_invite_rejected(self):
		invitee = _make_user("invitee@example.com")
		invitation = self._invite(
			"invitee@example.com",
			sent_at=timezone.now() - timedelta(days=ProjectInvitation.expiry_days() + 1),
		)
		self.client.force_login(invitee)
		self.client.post(reverse("core:accept_invite", args=[invitation.key]))
		self.assertFalse(ProjectMembership.objects.filter(project=self.project, user=invitee).exists())
		invitation.refresh_from_db()
		self.assertEqual(invitation.status, "expired")

	def test_accept_invite_for_existing_member_does_not_500(self):
		invitation = self._invite("member@example.com", role="manager")
		self.client.force_login(self.member)
		response = self.client.post(reverse("core:accept_invite", args=[invitation.key]))
		self.assertEqual(response.status_code, 302)
		self.member_ms.refresh_from_db()
		self.assertEqual(self.member_ms.role, "member")

	def test_legacy_owner_invite_from_manager_rejected(self):
		invitee = _make_user("invitee@example.com")
		invitation = self._invite("invitee@example.com", role="owner", inviter=self.manager)
		self.client.force_login(invitee)
		self.client.post(reverse("core:accept_invite", args=[invitation.key]))
		self.assertFalse(ProjectMembership.objects.filter(project=self.project, user=invitee).exists())


class AttributeAccessSecurityTests(TestCase):
	def setUp(self):
		self.owner = _make_user("owner@example.com")
		self.outsider = _make_user("outsider@example.com")
		self.project = _make_project(self.owner)
		self.study = _make_study(self.project, self.owner)
		self.global_attr = Attribute.objects.create(
			variable_name="temp", variable_type="float", category="climate", study=None,
		)
		self.study.variables.add(self.global_attr)

	def _update(self, user, attribute):
		self.client.force_login(user)
		return self.client.post(
			reverse("core:update_attribute", args=[attribute.pk]),
			data=json.dumps({"description": "tampered"}),
			content_type="application/json",
		)

	def test_non_member_cannot_update_studyless_attribute(self):
		response = self._update(self.outsider, self.global_attr)
		self.assertEqual(response.status_code, 403)
		self.global_attr.refresh_from_db()
		self.assertNotEqual(self.global_attr.description, "tampered")

	def test_unlinked_studyless_attribute_cannot_be_updated(self):
		orphan = Attribute.objects.create(variable_name="x", variable_type="int", category="health")
		response = self._update(self.owner, orphan)
		self.assertEqual(response.status_code, 403)

	def test_member_can_update_studyless_attribute_in_own_study(self):
		response = self._update(self.owner, self.global_attr)
		self.assertEqual(response.status_code, 200)
		self.global_attr.refresh_from_db()
		self.assertEqual(self.global_attr.description, "tampered")

	def test_shared_attribute_across_projects_cannot_be_updated(self):
		other_project = _make_project(self.outsider, name="Other")
		other_study = _make_study(other_project, self.outsider, name="Other study")
		other_study.variables.add(self.global_attr)
		response = self._update(self.outsider, self.global_attr)
		self.assertEqual(response.status_code, 403)


@override_settings(MEDIA_ROOT=_TEMP_MEDIA)
class UploadedFileAccessSecurityTests(TestCase):
	@classmethod
	def tearDownClass(cls):
		super().tearDownClass()
		shutil.rmtree(_TEMP_MEDIA, ignore_errors=True)

	def setUp(self):
		self.owner = _make_user("owner@example.com")
		self.outsider = _make_user("outsider@example.com")
		self.project = _make_project(self.owner)
		self.study = _make_study(self.project, self.owner)
		self.study.codebook.save("codebook.csv", ContentFile(b"variable_name\nage\n"), save=False)
		self.study.protocol_file.save("protocol.txt", ContentFile(b"protocol"), save=False)
		self.study.save()

	def test_upload_paths_are_randomised(self):
		parts = self.study.codebook.name.split("/")
		self.assertEqual(parts[:2], ["studies", "codebooks"])
		self.assertEqual(parts[-1], "codebook.csv")
		self.assertEqual(len(parts[-2]), 32)  # uuid4 hex
		self.assertEqual(self.study.codebook_filename, "codebook.csv")

	def test_non_member_cannot_download_codebook_or_protocol(self):
		self.client.force_login(self.outsider)
		for name in ("core:download_codebook", "core:download_protocol"):
			response = self.client.get(reverse(name, args=[self.study.pk]))
			self.assertEqual(response.status_code, 404, name)

	def test_member_can_download_protocol(self):
		self.client.force_login(self.owner)
		response = self.client.get(reverse("core:download_protocol", args=[self.study.pk]))
		self.assertEqual(response.status_code, 200)
		self.assertEqual(b"".join(response.streaming_content), b"protocol")

	def test_study_detail_links_use_protected_views(self):
		self.client.force_login(self.owner)
		response = self.client.get(reverse("core:study_detail", args=[self.study.pk]))
		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, self.study.codebook.name)
		self.assertNotContains(response, self.study.protocol_file.name)
		self.assertContains(response, reverse("core:download_protocol", args=[self.study.pk]))

	def test_non_member_cannot_export_raw_data(self):
		from health.models import RawDataFile

		raw = RawDataFile(
			study=self.study,
			original_filename="raw.csv",
			file_format="csv",
			file_size=4,
			uploaded_by=self.owner,
		)
		raw.file.save("raw.csv", ContentFile(b"a,b\n"), save=False)
		raw.save()
		self.client.force_login(self.outsider)
		response = self.client.get(reverse("health:export_raw_data", args=[raw.pk]))
		self.assertIn(response.status_code, (403, 404))

	def test_media_url_not_served(self):
		from config import urls as root_urls

		try:
			with override_settings(DEBUG=True, MEDIA_URL="/media/"):
				clear_url_caches()
				reload(root_urls)
				response = self.client.get("/media/" + self.study.codebook.name)
		finally:
			clear_url_caches()
			reload(root_urls)
		self.assertEqual(response.status_code, 404)
