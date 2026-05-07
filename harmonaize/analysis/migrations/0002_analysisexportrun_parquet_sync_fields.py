from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_study_documents"),
        ("analysis", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="analysisexportrun",
            name="artifact_manifest",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="analysisexportrun",
            name="generated_parquet_files",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="analysisexportrun",
            name="processed_target_studies",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="analysisexportrun",
            name="project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="analysis_export_runs",
                to="core.project",
            ),
        ),
        migrations.AddField(
            model_name="analysisexportrun",
            name="synced_projects_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="analysisexportrun",
            name="synced_users_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="analysisexportrun",
            name="uploaded_objects_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
