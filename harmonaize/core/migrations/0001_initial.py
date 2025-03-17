# Generated by Django 5.0.13 on 2025-03-17 19:25

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Attribute",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        help_text="Name of the attribute (e.g. 'blood_pressure_systolic').",
                        max_length=200,
                    ),
                ),
                (
                    "display_name",
                    models.CharField(
                        blank=True,
                        help_text="A user-friendly label (e.g. 'Systolic Blood Pressure').",
                        max_length=200,
                    ),
                ),
                (
                    "category",
                    models.CharField(
                        blank=True,
                        help_text="e.g., 'clinical', 'climate', 'demographic', etc.",
                        max_length=100,
                    ),
                ),
                (
                    "unit",
                    models.CharField(
                        blank=True,
                        help_text="Unit of measurement if numeric (e.g. 'mmHg', 'Celsius', etc.)",
                        max_length=50,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="Patient",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "unique_id",
                    models.CharField(
                        help_text="A unique identifier for the patient (not necessarily PHI).",
                        max_length=100,
                        unique=True,
                    ),
                ),
                ("date_of_birth", models.DateField(blank=True, null=True)),
                (
                    "sex",
                    models.CharField(
                        blank=True,
                        help_text="e.g., 'M', 'F', 'Other', or 'Unknown'",
                        max_length=10,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="TimeDimension",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "timestamp",
                    models.DateTimeField(
                        blank=True,
                        help_text="If you have an exact timestamp for the observation.",
                        null=True,
                    ),
                ),
                ("start_date", models.DateTimeField(blank=True, null=True)),
                ("end_date", models.DateTimeField(blank=True, null=True)),
                (
                    "resolution",
                    models.CharField(
                        blank=True,
                        help_text="E.g., 'daily', 'monthly', 'annual' for climate data.",
                        max_length=50,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="Location",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        blank=True,
                        help_text="Place name (e.g. clinic, city, region).",
                        max_length=200,
                    ),
                ),
                ("country", models.CharField(blank=True, max_length=100)),
                (
                    "region",
                    models.CharField(
                        blank=True,
                        help_text="State/province or large administrative area",
                        max_length=100,
                    ),
                ),
                ("latitude", models.FloatField(blank=True, null=True)),
                ("longitude", models.FloatField(blank=True, null=True)),
                (
                    "altitude",
                    models.FloatField(
                        blank=True, help_text="Altitude in meters", null=True
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "parent_location",
                    models.ForeignKey(
                        blank=True,
                        help_text="Optional parent location for hierarchical grouping.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="sub_locations",
                        to="core.location",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Observation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "numeric_value",
                    models.FloatField(
                        blank=True,
                        help_text="If the observation is numeric.",
                        null=True,
                    ),
                ),
                (
                    "text_value",
                    models.TextField(
                        blank=True,
                        help_text="If the observation is textual or categorical.",
                    ),
                ),
                (
                    "data_type",
                    models.CharField(
                        blank=True,
                        help_text="e.g., 'float', 'int', 'string', 'categorical'.",
                        max_length=50,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "attribute",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="observations",
                        to="core.attribute",
                    ),
                ),
                (
                    "location",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="observations",
                        to="core.location",
                    ),
                ),
                (
                    "patient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="observations",
                        to="core.patient",
                    ),
                ),
                (
                    "time",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="observations",
                        to="core.timedimension",
                    ),
                ),
            ],
        ),
    ]
