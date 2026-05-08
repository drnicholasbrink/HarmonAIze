from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.http import HttpResponse
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from core.models import Project
from health.dataset_exports import safe_slug

from .forms import AnalysisDatasetTemplateForm
from .forms import AnalysisSyncForm
from .forms import AnalysisScheduleForm
from .models import AnalysisDatasetTemplate
from .models import AnalysisExportRun
from .models import AnalysisScheduleConfiguration
from .services import AnalysisExportService
from .services import get_analysis_service_status
from .services import sync_scheduler_configuration
from .tasks import run_analysis_export

TEMPLATE_STATE_CHOICES = {"all", "active", "inactive"}


def _project_slug(project) -> str:
	if not project:
		return "project"
	return safe_slug(project.name, f"project-{project.id}")


def _target_slug(study) -> str:
	if not study:
		return "target-study"
	return safe_slug(study.name, f"target-study-{study.id}")


def _template_folder_path(project, target_study, template_slug: str) -> str:
	return f"{_project_slug(project)}/{template_slug}/data"


def _normalize_template_state(state: str | None) -> str:
	if state in TEMPLATE_STATE_CHOICES:
		return state
	return "all"


def _get_editable_template_queryset(user):
	templates_qs = AnalysisDatasetTemplate.objects.select_related(
		"project",
		"target_study",
		"created_by",
	).prefetch_related(
		"outcome_attributes",
		"confounder_attributes",
		"climate_attributes",
		"location_attributes",
	).order_by("-updated_at")
	if not user.is_superuser and not user.groups.filter(name="analysis_admin").exists():
		templates_qs = templates_qs.filter(project__in=_manageable_projects(user))
	return templates_qs


def _get_template_instance(user, template_id):
	if not template_id:
		return None
	try:
		return _get_editable_template_queryset(user).get(pk=template_id)
	except AnalysisDatasetTemplate.DoesNotExist:
		return None


def _template_remote_binding(template: AnalysisDatasetTemplate | None):
	if not template or not getattr(template, "pk", None):
		return None
	service = AnalysisExportService()
	return service.get_template_object_binding(template)


def _delete_remote_binding(binding) -> str:
	if not binding:
		return ""
	service = AnalysisExportService()
	try:
		service.delete_object_binding(
			project_name=binding["project_name"],
			object_name=binding["object_name"],
		)
	except RuntimeError as exc:
		return str(exc)
	return ""


def _apply_form_error_classes(form):
	for field_name in form.errors:
		if field_name == "__all__" or field_name not in form.fields:
			continue
		widget = form.fields[field_name].widget
		existing_classes = widget.attrs.get("class", "")
		if "is-invalid" not in existing_classes.split():
			widget.attrs["class"] = f"{existing_classes} is-invalid".strip()


def _has_analysis_admin_access(user) -> bool:
	if user.is_superuser:
		return True
	if user.groups.filter(name="analysis_admin").exists():
		return True
	return Project.objects.filter(
		memberships__user=user,
		memberships__role__in=["owner", "manager"],
	).exists()


def _manageable_projects(user):
	if user.is_superuser:
		return Project.objects.all().order_by("name")

	return (
		Project.objects.filter(
			memberships__user=user,
			memberships__role__in=["owner", "manager"],
		)
		.distinct()
		.order_by("name")
	)


def _dashboard_context(user, template_form=None, template_state="all"):
	def _selected_ids(form_obj, field_name):
		if form_obj is None:
			return []
		if form_obj.is_bound:
			if hasattr(form_obj.data, "getlist"):
				return form_obj.data.getlist(field_name)
			value = form_obj.data.get(field_name, [])
			if value in (None, ""):
				return []
			if isinstance(value, (list, tuple, set)):
				return [str(item) for item in value]
			return [str(value)]
		initial_value = form_obj.initial.get(field_name) or getattr(form_obj.instance, field_name, None)
		if hasattr(initial_value, "values_list"):
			return [str(value) for value in initial_value.values_list("pk", flat=True)]
		if isinstance(initial_value, (list, tuple, set)):
			return [str(getattr(value, "pk", value)) for value in initial_value]
		return []

	schedule_config, _ = AnalysisScheduleConfiguration.objects.get_or_create(name="default")
	schedule_form = AnalysisScheduleForm(instance=schedule_config)
	sync_form = AnalysisSyncForm(user=user)
	template_form = template_form or AnalysisDatasetTemplateForm(user=user)
	template_state = _normalize_template_state(template_state)

	runs_qs = AnalysisExportRun.objects.order_by("-created_at")
	all_templates_qs = _get_editable_template_queryset(user)
	analysis_service = AnalysisExportService()
	if not user.is_superuser and not user.groups.filter(name="analysis_admin").exists():
		manageable_projects = _manageable_projects(user)
		runs_qs = runs_qs.filter(project__in=manageable_projects)

	templates_qs = all_templates_qs
	if template_state == "active":
		templates_qs = templates_qs.filter(is_active=True)
	elif template_state == "inactive":
		templates_qs = templates_qs.filter(is_active=False)

	template_sync_index: dict[int, dict] = {}
	for run in runs_qs[:50]:
		for artifact in run.artifact_manifest or []:
			template_id = artifact.get("template_id")
			if not template_id or template_id in template_sync_index:
				continue
			template_sync_index[template_id] = {
				"run": run,
				"artifact": artifact,
			}

	template_rows = []
	for template in templates_qs[:20]:
		outcomes = list(template.outcome_attributes.all())
		binding = analysis_service.get_template_object_binding(template)
		sync_entry = template_sync_index.get(template.id)
		sync_state = "never"
		sync_label = "Not synced"
		sync_detail = "This template has not been synced to Armadillo yet."
		sync_badge_class = "status-pending"
		sync_dot_class = "is-inactive"
		last_synced_at = None

		if sync_entry:
			run = sync_entry["run"]
			artifact = sync_entry["artifact"]
			artifact_object_name = artifact.get("object_name", "")
			last_synced_at = run.completed_at or run.updated_at or run.created_at
			is_current_object = artifact_object_name == binding["object_name"]
			updated_after_sync = bool(last_synced_at and template.updated_at and template.updated_at > last_synced_at)

			if run.status == AnalysisExportRun.STATUS_RUNNING:
				sync_state = "running"
				sync_label = "Syncing"
				sync_detail = "A sync run is currently updating this template in Armadillo."
				sync_badge_class = "status-running"
				sync_dot_class = "is-running"
			elif run.status == AnalysisExportRun.STATUS_PENDING:
				sync_state = "pending"
				sync_label = "Queued"
				sync_detail = "This template is queued for sync to Armadillo."
				sync_badge_class = "status-pending"
				sync_dot_class = "is-pending"
			elif run.status == AnalysisExportRun.STATUS_FAILED:
				sync_state = "failed"
				sync_label = "Failed"
				sync_detail = "The most recent sync attempt for this template failed."
				sync_badge_class = "status-failed"
				sync_dot_class = "is-failed"
			elif is_current_object and not updated_after_sync:
				sync_state = "synced"
				sync_label = "Synced"
				sync_detail = "The current template definition matches the latest synced object in Armadillo."
				sync_badge_class = "status-succeeded"
				sync_dot_class = "is-active"
			else:
				sync_state = "stale"
				sync_label = "Needs resync"
				sync_detail = "The template changed after the last sync, or the synced object path is outdated."
				sync_badge_class = "status-running"
				sync_dot_class = "is-running"

		template_rows.append(
			{
				"template": template,
				"table_path": binding["table_path"],
				"object_path": binding["object_path"],
				"primary_outcome": (
					(outcomes[0].display_name or outcomes[0].variable_name)
					if outcomes
					else "Not set"
				),
				"confounder_count": len(template.confounder_attributes.all()),
				"climate_count": len(template.climate_attributes.all()),
				"location_count": len(template.location_attributes.all()),
				"sync_state": sync_state,
				"sync_label": sync_label,
				"sync_detail": sync_detail,
				"sync_badge_class": sync_badge_class,
				"sync_dot_class": sync_dot_class,
				"last_synced_at": last_synced_at,
			},
		)

	export_audit_entries = []
	latest_run = runs_qs.first()
	for run in runs_qs[:10]:
		for artifact in run.artifact_manifest or []:
			export_audit_entries.append(
				{
					"run": run,
					"artifact": artifact,
					"table_path": f"{_project_slug(run.project)}/{artifact.get('dataset_slug', '')}/data",
					"object_path": f"{_project_slug(run.project)}/{artifact.get('object_name', '')}",
				},
			)
		if len(export_audit_entries) >= 20:
			break

	return {
		"schedule_form": schedule_form,
		"sync_form": sync_form,
		"template_form": template_form,
		"schedule_config": schedule_config,
		"latest_run": latest_run,
		"latest_table_path": (
			f"{_project_slug(latest_run.project)}/{latest_run.artifact_manifest[0].get('dataset_slug', '')}/data"
			if latest_run and latest_run.artifact_manifest
			else ""
		),
		"latest_object_path": (
			f"{_project_slug(latest_run.project)}/{latest_run.artifact_manifest[0].get('object_name', '')}"
			if latest_run and latest_run.artifact_manifest
			else ""
		),
		"recent_runs": runs_qs[:10],
		"dataset_templates": templates_qs[:12],
		"template_rows": template_rows,
		"template_counts": {
			"all": all_templates_qs.count(),
			"active": all_templates_qs.filter(is_active=True).count(),
			"inactive": all_templates_qs.filter(is_active=False).count(),
		},
		"template_state": template_state,
		"selected_template_id": getattr(template_form.instance, "pk", None),
		"export_audit_entries": export_audit_entries[:20],
		"selected_source_study_ids": _selected_ids(template_form, "source_studies"),
		"selected_outcome_ids": _selected_ids(template_form, "outcome_attributes"),
		"selected_confounder_ids": _selected_ids(template_form, "confounder_attributes"),
		"selected_climate_ids": _selected_ids(template_form, "climate_attributes"),
		"selected_location_ids": _selected_ids(template_form, "location_attributes"),
	}


def _render_template_manager(request, user, template_state="all", template_form=None):
	return render(
		request,
		"analysis/partials/template_manager.html",
		_dashboard_context(
			user,
			template_form=template_form,
			template_state=template_state,
		),
	)


def _render_template_builder(request, template_form, success_message="", warning_message="", status_code=200):
	def _resolved_object(field_name, queryset):
		if template_form.is_bound:
			object_id = template_form.data.get(field_name)
			if object_id:
				return queryset.filter(pk=object_id).first()
		return getattr(template_form.instance, field_name, None)

	def _selected_ids(field_name):
		if template_form.is_bound:
			if hasattr(template_form.data, "getlist"):
				return template_form.data.getlist(field_name)
			value = template_form.data.get(field_name, [])
			if value in (None, ""):
				return []
			if isinstance(value, (list, tuple, set)):
				return [str(item) for item in value]
			return [str(value)]
		initial_value = template_form.initial.get(field_name) or getattr(template_form.instance, field_name, None)
		if hasattr(initial_value, "values_list"):
			return [str(value) for value in initial_value.values_list("pk", flat=True)]
		if isinstance(initial_value, (list, tuple, set)):
			return [str(getattr(value, "pk", value)) for value in initial_value]
		return []

	project_obj = _resolved_object("project", template_form.fields["project"].queryset)
	target_obj = _resolved_object("target_study", template_form.fields["target_study"].queryset)

	return render(
		request,
		"analysis/partials/template_builder.html",
		{
			"template_form": template_form,
			"template_slug": template_form.auto_name,
			"template_folder_path": _template_folder_path(
				project_obj,
				target_obj,
				template_form.auto_name,
			),
			"template_success_message": success_message,
			"selected_source_study_ids": _selected_ids("source_studies"),
			"selected_outcome_ids": _selected_ids("outcome_attributes"),
			"selected_confounder_ids": _selected_ids("confounder_attributes"),
			"selected_climate_ids": _selected_ids("climate_attributes"),
			"selected_location_ids": _selected_ids("location_attributes"),
			"editing_template": template_form.instance if getattr(template_form.instance, "pk", None) else None,
			"duplicate_template": getattr(template_form, "duplicate_template", None),
			"duplicate_is_exact": getattr(template_form, "duplicate_is_exact", False),
			"template_warning_message": warning_message,
		},
		status=status_code,
	)


def _render_dashboard_status(request, user):
	return render(
		request,
		"analysis/partials/dashboard_status.html",
		_dashboard_context(user),
	)


@login_required
def analysis_dashboard(request):
	if not _has_analysis_admin_access(request.user):
		return render(
			request,
			"analysis/access_denied.html",
			status=403,
		)
	return render(request, "analysis/dashboard.html", _dashboard_context(request.user))


@login_required
def dataset_template_builder_partial(request):
	if not _has_analysis_admin_access(request.user):
		return HttpResponseForbidden("You do not have permission to manage dataset templates.")

	form_field_names = set(AnalysisDatasetTemplateForm.base_fields.keys())
	bound_keys = set(request.GET.keys()) & form_field_names
	form_data = request.GET if bound_keys else None
	template_instance = _get_template_instance(request.user, request.GET.get("template_id"))
	template_form = AnalysisDatasetTemplateForm(
		form_data,
		user=request.user,
		instance=template_instance,
	)
	return _render_template_builder(request, template_form)


@login_required
def template_manager_partial(request):
	if not _has_analysis_admin_access(request.user):
		return HttpResponseForbidden("You do not have permission to manage dataset templates.")
	template_state = _normalize_template_state(request.GET.get("state"))
	return _render_template_manager(request, request.user, template_state=template_state)


@login_required
def dashboard_status_partial(request):
	if not _has_analysis_admin_access(request.user):
		return HttpResponse("", status=403)
	return _render_dashboard_status(request, request.user)


@login_required
@require_POST
def run_export_now(request):
	if not _has_analysis_admin_access(request.user):
		return HttpResponseForbidden("You do not have permission to run exports.")
	sync_form = AnalysisSyncForm(request.POST, user=request.user)
	if not sync_form.is_valid():
		messages.error(request, "Please select a valid project before starting sync.")
		return redirect("analysis:dashboard")

	service_status = get_analysis_service_status()
	armadillo_status = service_status.get("armadillo", {})
	if not armadillo_status.get("reachable"):
		messages.error(
			request,
			"Analysis sync is unavailable because Armadillo is not reachable. "
			f"{armadillo_status.get('detail', '')}",
		)
		return redirect("analysis:dashboard")

	project = sync_form.cleaned_data["project"]
	if not AnalysisDatasetTemplate.objects.filter(project=project, is_active=True).exists():
		messages.error(
			request,
			"Create at least one active dataset template for this project before running sync.",
		)
		return redirect("analysis:dashboard")

	run = AnalysisExportRun.objects.create(
		status=AnalysisExportRun.STATUS_PENDING,
		trigger_mode=AnalysisExportRun.TRIGGER_MANUAL,
		initiated_by=request.user,
		project=project,
	)

	async_result = run_analysis_export.delay(run.id)
	run.task_id = async_result.id
	run.save(update_fields=["task_id", "updated_at"])

	messages.success(request, f"Analysis sync queued for project '{project.name}'.")
	return redirect("analysis:dashboard")


@login_required
@require_POST
def update_schedule(request):
	if not _has_analysis_admin_access(request.user):
		return HttpResponseForbidden("You do not have permission to update schedule settings.")

	schedule_config, _ = AnalysisScheduleConfiguration.objects.get_or_create(name="default")
	form = AnalysisScheduleForm(request.POST, instance=schedule_config)
	if not form.is_valid():
		messages.error(request, "Schedule settings could not be saved. Please check the inputs.")
		return redirect("analysis:dashboard")

	updated = form.save(commit=False)
	updated.updated_by = request.user
	updated.save()
	sync_scheduler_configuration(updated)

	messages.success(request, "Analysis schedule updated successfully.")
	return redirect("analysis:dashboard")


@login_required
@require_POST
def save_dataset_template(request):
	if not _has_analysis_admin_access(request.user):
		return HttpResponseForbidden("You do not have permission to manage dataset templates.")

	template_instance = _get_template_instance(request.user, request.POST.get("template_id"))
	previous_binding = _template_remote_binding(template_instance)
	template_form = AnalysisDatasetTemplateForm(
		request.POST,
		user=request.user,
		instance=template_instance,
	)
	if not template_form.is_valid():
		_apply_form_error_classes(template_form)
		if not getattr(request, "htmx", False):
			messages.error(request, "Dataset template could not be saved. Please check the fields below.")
		if getattr(request, "htmx", False):
			return _render_template_builder(
				request,
				template_form,
				warning_message="Dataset template could not be saved. Please check the highlighted fields below.",
				status_code=200,
			)
		return render(
			request,
			"analysis/dashboard.html",
			_dashboard_context(request.user, template_form=template_form),
			status=400,
		)

	dataset_template = template_form.save(commit=False)
	dataset_template.name = template_form.auto_name
	if not dataset_template.created_by_id:
		dataset_template.created_by = request.user
	try:
		dataset_template.save()
	except IntegrityError:
		duplicate_template = AnalysisDatasetTemplate.objects.filter(
			project=dataset_template.project,
			target_study=dataset_template.target_study,
			name=dataset_template.name,
		).exclude(pk=dataset_template.pk).first()
		if duplicate_template:
			template_form.attach_duplicate_error(
				duplicate_template,
				exact_match=False,
			)
			_apply_form_error_classes(template_form)
			if not getattr(request, "htmx", False):
				messages.error(
					request,
					"That dataset template already exists. Open the existing template to update it.",
				)
			if getattr(request, "htmx", False):
				return _render_template_builder(
					request,
					template_form,
					warning_message="That dataset template already exists. Open the existing template to update it.",
					status_code=200,
				)
			return render(
				request,
				"analysis/dashboard.html",
				_dashboard_context(request.user, template_form=template_form),
				status=400,
			)
		raise
	template_form.save_m2m()
	remote_warning = ""
	updated_binding = _template_remote_binding(dataset_template)
	if previous_binding and updated_binding:
		if (
			previous_binding["project_name"],
			previous_binding["object_name"],
		) != (
			updated_binding["project_name"],
			updated_binding["object_name"],
		):
			remote_warning = _delete_remote_binding(previous_binding)

	success_message = (
		f"Dataset template '{dataset_template.name}' saved for "
		f"{dataset_template.target_study.name}."
	)
	if not getattr(request, "htmx", False):
		messages.success(
			request,
			success_message,
		)
	if remote_warning:
		if not getattr(request, "htmx", False):
			messages.warning(
				request,
				"Template saved, but the previous Armadillo object could not be removed automatically. "
				f"{remote_warning}",
			)
	if getattr(request, "htmx", False):
		saved_form = AnalysisDatasetTemplateForm(
			user=request.user,
			instance=dataset_template,
		)
		response = _render_template_builder(
			request,
			saved_form,
			success_message=success_message,
			warning_message=(
				"Previous synced object could not be removed automatically. "
				f"{remote_warning}"
				if remote_warning
				else ""
			),
		)
		response["HX-Trigger"] = "analysis-template-saved"
		return response
	return redirect("analysis:dashboard")


@login_required
@require_POST
def delete_dataset_template(request):
	if not _has_analysis_admin_access(request.user):
		return HttpResponseForbidden("You do not have permission to manage dataset templates.")

	template_instance = _get_template_instance(request.user, request.POST.get("template_id"))
	if not template_instance:
		return HttpResponse("Dataset template not found.", status=404)

	deleted_name = template_instance.name
	deleted_target = template_instance.target_study.name
	initial_project_id = template_instance.project_id
	remote_binding = _template_remote_binding(template_instance)
	template_instance.delete()
	remote_warning = _delete_remote_binding(remote_binding)
	success_message = f"Dataset template '{deleted_name}' deleted from {deleted_target}."
	if not getattr(request, "htmx", False):
		messages.success(request, success_message)
	if remote_warning:
		if not getattr(request, "htmx", False):
			messages.warning(
				request,
				"Template deleted locally, but the synced Armadillo object could not be removed automatically. "
				f"{remote_warning}",
			)

	if getattr(request, "htmx", False):
		fresh_form = AnalysisDatasetTemplateForm(
			user=request.user,
			initial={"project": initial_project_id},
		)
		response = _render_template_builder(
			request,
			fresh_form,
			success_message=success_message,
			warning_message=(
				"Synced Armadillo object could not be removed automatically. "
				f"{remote_warning}"
				if remote_warning
				else ""
			),
		)
		response["HX-Trigger"] = "analysis-template-deleted"
		return response

	return redirect("analysis:dashboard")
