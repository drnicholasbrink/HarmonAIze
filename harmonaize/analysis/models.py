from django.db import models
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta

from health.dataset_exports import safe_slug


class AnalysisSyncLock(models.Model):
	"""Single-flight lock to prevent overlapping exports."""

	lock_name = models.CharField(max_length=100, unique=True, default="default")
	locked_by_task_id = models.CharField(max_length=255, blank=True)
	locked_at = models.DateTimeField(null=True, blank=True)
	lock_expires_at = models.DateTimeField(null=True, blank=True)

	class Meta:
		verbose_name = "Analysis sync lock"
		verbose_name_plural = "Analysis sync locks"

	def __str__(self):
		return f"{self.lock_name} (locked_by={self.locked_by_task_id or 'none'})"

	@classmethod
	def acquire(cls, lock_name: str, task_id: str, ttl_seconds: int = 900) -> bool:
		"""Acquire lock if not currently held or if the lock has expired."""
		with transaction.atomic():
			now = timezone.now()
			lock, _ = cls.objects.select_for_update().get_or_create(lock_name=lock_name)

			if lock.lock_expires_at and lock.lock_expires_at > now:
				return False

			lock.locked_by_task_id = task_id
			lock.locked_at = now
			lock.lock_expires_at = now + timedelta(seconds=ttl_seconds)
			lock.save(update_fields=["locked_by_task_id", "locked_at", "lock_expires_at"])
			return True

	@classmethod
	def release(cls, lock_name: str, task_id: str) -> None:
		"""Release lock only when held by the current task."""
		try:
			lock = cls.objects.get(lock_name=lock_name)
		except cls.DoesNotExist:
			return

		if lock.locked_by_task_id != task_id:
			return

		lock.locked_by_task_id = ""
		lock.locked_at = None
		lock.lock_expires_at = None
		lock.save(update_fields=["locked_by_task_id", "locked_at", "lock_expires_at"])


class AnalysisExportRun(models.Model):
	"""Tracks each export execution and its watermark progression."""

	STATUS_PENDING = "pending"
	STATUS_RUNNING = "running"
	STATUS_SUCCEEDED = "succeeded"
	STATUS_FAILED = "failed"

	STATUS_CHOICES = [
		(STATUS_PENDING, "Pending"),
		(STATUS_RUNNING, "Running"),
		(STATUS_SUCCEEDED, "Succeeded"),
		(STATUS_FAILED, "Failed"),
	]

	TRIGGER_MANUAL = "manual"
	TRIGGER_SCHEDULED = "scheduled"
	TRIGGER_CHOICES = [
		(TRIGGER_MANUAL, "Manual"),
		(TRIGGER_SCHEDULED, "Scheduled"),
	]

	status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
	trigger_mode = models.CharField(max_length=20, choices=TRIGGER_CHOICES, default=TRIGGER_MANUAL)
	initiated_by = models.ForeignKey(
		"users.User",
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name="analysis_export_runs",
	)
	project = models.ForeignKey(
		"core.Project",
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name="analysis_export_runs",
	)
	task_id = models.CharField(max_length=255, blank=True)
	watermark_before = models.DateTimeField(null=True, blank=True)
	watermark_after = models.DateTimeField(null=True, blank=True)
	processed_studies = models.PositiveIntegerField(default=0)
	processed_attributes = models.PositiveIntegerField(default=0)
	processed_target_studies = models.PositiveIntegerField(default=0)
	generated_parquet_files = models.PositiveIntegerField(default=0)
	synced_users_count = models.PositiveIntegerField(default=0)
	synced_projects_count = models.PositiveIntegerField(default=0)
	uploaded_objects_count = models.PositiveIntegerField(default=0)
	artifact_manifest = models.JSONField(default=list, blank=True)
	started_at = models.DateTimeField(null=True, blank=True)
	completed_at = models.DateTimeField(null=True, blank=True)
	error_message = models.TextField(blank=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["-created_at"]
		permissions = [
			("manage_analysis_exports", "Can manage analysis exports"),
		]

	def __str__(self):
		project_name = self.project.name if self.project_id else "all-projects"
		return f"Analysis export #{self.pk} [{project_name}] ({self.status})"


class AnalysisDatasetTemplate(models.Model):
	"""Saved analysis-ready dataset definition for target-study-centric syncs."""

	DATASET_SHAPE_CHOICES = [
		("long", "Long"),
		("wide", "Wide"),
	]

	name = models.CharField(max_length=120)
	project = models.ForeignKey(
		"core.Project",
		on_delete=models.CASCADE,
		related_name="analysis_dataset_templates",
	)
	target_study = models.ForeignKey(
		"core.Study",
		on_delete=models.CASCADE,
		related_name="analysis_dataset_templates",
	)
	source_studies = models.ManyToManyField(
		"core.Study",
		blank=True,
		related_name="analysis_dataset_template_sources",
	)
	outcome_attributes = models.ManyToManyField(
		"core.Attribute",
		blank=True,
		related_name="analysis_dataset_template_outcomes",
	)
	confounder_attributes = models.ManyToManyField(
		"core.Attribute",
		blank=True,
		related_name="analysis_dataset_template_confounders",
	)
	climate_attributes = models.ManyToManyField(
		"core.Attribute",
		blank=True,
		related_name="analysis_dataset_template_climate",
	)
	location_attributes = models.ManyToManyField(
		"core.Attribute",
		blank=True,
		related_name="analysis_dataset_template_locations",
	)
	dataset_shape = models.CharField(
		max_length=10,
		choices=DATASET_SHAPE_CHOICES,
		default="long",
	)
	max_lag_value = models.PositiveIntegerField(default=0)
	deidentify = models.BooleanField(default=True)
	is_active = models.BooleanField(default=True)
	notes = models.TextField(blank=True)
	created_by = models.ForeignKey(
		"users.User",
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name="analysis_dataset_templates_created",
	)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["project__name", "target_study__name", "name"]
		unique_together = ("project", "target_study", "name")

	def __str__(self):
		return f"{self.project.name} · {self.target_study.name} · {self.name}"

	def build_slug_name(self) -> str:
		shape_token = self.dataset_shape or "long"
		lag_token = f"lag{self.max_lag_value or 0}"
		study_slug = "target-study"
		if self.target_study_id:
			study_slug = safe_slug(self.target_study.name, f"target-study-{self.target_study_id}")
		base_slug = f"{study_slug}-{lag_token}-{shape_token}"
		return base_slug[:120]

	def clean(self):
		errors = {}
		if self.target_study_id and self.target_study.study_purpose != "target":
			errors["target_study"] = "Dataset templates must point to a target study."
		if self.project_id and self.target_study_id and self.target_study.project_id != self.project_id:
			errors["target_study"] = "Target study must belong to the selected project."
		if not self.name:
			self.name = self.build_slug_name()
		if errors:
			raise ValidationError(errors)

	def save(self, *args, **kwargs):
		if not self.name:
			self.name = self.build_slug_name()
		super().save(*args, **kwargs)


class AnalysisScheduleConfiguration(models.Model):
	"""Stores optional scheduler settings configured from the UI."""

	name = models.CharField(max_length=50, unique=True, default="default")
	enabled = models.BooleanField(default=False)
	interval_minutes = models.PositiveIntegerField(default=60)
	updated_by = models.ForeignKey(
		"users.User",
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name="analysis_schedule_updates",
	)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = "Analysis schedule configuration"
		verbose_name_plural = "Analysis schedule configurations"

	def __str__(self):
		state = "enabled" if self.enabled else "disabled"
		return f"{self.name} ({state}, every {self.interval_minutes} minutes)"

# Create your models here.
