from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("health", "0006_rawdatafile_checksum_rawdatafile_detected_columns_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="rawdatafile",
            name="transformation_status",
            field=models.CharField(
                choices=[
                    ("not_started", "Not Started"),
                    ("in_progress", "Transformation In Progress"),
                    ("completed", "Transformed"),
                    ("failed", "Transformation Failed"),
                ],
                default="not_started",
                help_text="Status of harmonisation transformation for this file",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="rawdatafile",
            name="transformation_message",
            field=models.TextField(
                blank=True,
                help_text="Notes or errors from the last harmonisation transformation run",
            ),
        ),
        migrations.AddField(
            model_name="rawdatafile",
            name="last_transformation_schema",
            field=models.ForeignKey(
                blank=True,
                help_text="The mapping schema used in the last harmonisation transformation",
                null=True,
                on_delete=models.SET_NULL,
                related_name="transformed_files",
                to="health.mappingschema",
            ),
        ),
        migrations.AddField(
            model_name="rawdatafile",
            name="transformation_started_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When harmonisation transformation started for this file",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="rawdatafile",
            name="transformed_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When harmonisation transformation completed for this file",
                null=True,
            ),
        ),
    ]
