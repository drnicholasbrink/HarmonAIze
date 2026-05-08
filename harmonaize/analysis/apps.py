from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _ensure_analysis_admin_group(sender, **kwargs):
    """Create the analysis admin group after migrations."""
    from django.contrib.auth.models import Group

    Group.objects.get_or_create(name="analysis_admin")


class AnalysisConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'analysis'

    def ready(self):
        post_migrate.connect(_ensure_analysis_admin_group, sender=self)
