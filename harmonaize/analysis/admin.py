from django.contrib import admin

from .models import AnalysisDatasetTemplate
from .models import AnalysisExportRun
from .models import AnalysisScheduleConfiguration
from .models import AnalysisSyncLock


@admin.register(AnalysisExportRun)
class AnalysisExportRunAdmin(admin.ModelAdmin):
	list_display = (
		"id",
		"status",
		"trigger_mode",
		"initiated_by",
		"processed_studies",
		"processed_attributes",
		"created_at",
	)
	list_filter = ("status", "trigger_mode", "created_at")
	search_fields = ("id", "task_id", "error_message")
	readonly_fields = (
		"status",
		"task_id",
		"watermark_before",
		"watermark_after",
		"processed_studies",
		"processed_attributes",
		"started_at",
		"completed_at",
		"error_message",
		"created_at",
		"updated_at",
	)


@admin.register(AnalysisDatasetTemplate)
class AnalysisDatasetTemplateAdmin(admin.ModelAdmin):
	list_display = (
		"name",
		"project",
		"target_study",
		"dataset_shape",
		"deidentify",
		"max_lag_value",
		"is_active",
		"updated_at",
	)
	list_filter = ("dataset_shape", "deidentify", "is_active", "project")
	search_fields = ("name", "project__name", "target_study__name")


@admin.register(AnalysisScheduleConfiguration)
class AnalysisScheduleConfigurationAdmin(admin.ModelAdmin):
	list_display = ("name", "enabled", "interval_minutes", "updated_by", "updated_at")
	list_filter = ("enabled", "updated_at")


@admin.register(AnalysisSyncLock)
class AnalysisSyncLockAdmin(admin.ModelAdmin):
	list_display = ("lock_name", "locked_by_task_id", "locked_at", "lock_expires_at")
