import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from core.models import Project

from .models import AnalysisExportRun
from .models import AnalysisSyncLock
from .services import AnalysisExportService

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def run_analysis_export(self, run_id: int) -> dict:
    """Execute a single export run with retry/backoff and single-flight lock."""
    task_id = self.request.id

    with transaction.atomic():
        run = AnalysisExportRun.objects.select_for_update().get(pk=run_id)
        run.status = AnalysisExportRun.STATUS_RUNNING
        run.task_id = task_id
        run.started_at = timezone.now()
        run.save(update_fields=["status", "task_id", "started_at", "updated_at"])

    if not AnalysisSyncLock.acquire(lock_name="analysis_export", task_id=task_id):
        run.status = AnalysisExportRun.STATUS_FAILED
        run.error_message = "Another export is already running."
        run.completed_at = timezone.now()
        run.save(
            update_fields=["status", "error_message", "completed_at", "updated_at"],
        )
        return {"success": False, "reason": "lock_not_acquired"}

    try:
        service = AnalysisExportService()
        if run.project_id is None:
            run.status = AnalysisExportRun.STATUS_FAILED
            run.error_message = "Export run must reference a project."
            run.completed_at = timezone.now()
            run.save(
                update_fields=["status", "error_message", "completed_at", "updated_at"],
            )
            return {"success": False, "reason": "missing_project"}

        project = Project.objects.get(pk=run.project_id)
        result = service.run_project_sync(project=project)

        run.status = AnalysisExportRun.STATUS_SUCCEEDED
        run.watermark_after = result.get("watermark_after")
        run.processed_studies = result.get("processed_studies", 0)
        run.processed_attributes = result.get("processed_attributes", 0)
        run.processed_target_studies = result.get("processed_target_studies", 0)
        run.generated_parquet_files = result.get("generated_parquet_files", 0)
        run.synced_users_count = result.get("synced_users_count", 0)
        run.synced_projects_count = result.get("synced_projects_count", 0)
        run.uploaded_objects_count = result.get("uploaded_objects_count", 0)
        run.artifact_manifest = result.get("artifact_manifest", [])
        run.completed_at = timezone.now()
        run.error_message = ""
        run.save(
            update_fields=[
                "status",
                "watermark_after",
                "processed_studies",
                "processed_attributes",
                "processed_target_studies",
                "generated_parquet_files",
                "synced_users_count",
                "synced_projects_count",
                "uploaded_objects_count",
                "artifact_manifest",
                "completed_at",
                "error_message",
                "updated_at",
            ],
        )

        logger.info(
            "Analysis export run %s completed (studies=%s, attributes=%s)",
            run.id,
            run.processed_studies,
            run.processed_attributes,
        )
    except Exception as exc:
        run.status = AnalysisExportRun.STATUS_FAILED
        run.error_message = str(exc)
        run.completed_at = timezone.now()
        run.save(
            update_fields=["status", "error_message", "completed_at", "updated_at"],
        )
        logger.exception("Analysis export run %s failed", run.id)
        raise
    else:
        return {"success": True, **result}
    finally:
        AnalysisSyncLock.release(lock_name="analysis_export", task_id=task_id)


@shared_task
def run_scheduled_analysis_export() -> dict:
    """Create and queue a scheduled run from django-celery-beat."""
    created_runs: list[dict] = []
    for project in Project.objects.filter(studies__study_purpose="target").distinct():
        run = AnalysisExportRun.objects.create(
            status=AnalysisExportRun.STATUS_PENDING,
            trigger_mode=AnalysisExportRun.TRIGGER_SCHEDULED,
            project=project,
        )
        async_result = run_analysis_export.delay(run.id)
        run.task_id = async_result.id
        run.save(update_fields=["task_id", "updated_at"])
        created_runs.append(
            {
                "run_id": run.id,
                "task_id": async_result.id,
                "project_id": project.id,
            },
        )

    return {"queued_runs": created_runs}
