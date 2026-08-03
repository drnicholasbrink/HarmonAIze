from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('geolocation', '0004_add_user_provided_source'),
    ]

    operations = [
        migrations.AddField(
            model_name='geocodingresult',
            name='source_comparison_metrics',
            field=models.JSONField(
                blank=True,
                null=True,
                help_text=(
                    'Computed after validation: per-source distance to ground truth, accuracy flags, '
                    'and inter-source pairwise distances. Used for research/paper metrics.'
                ),
            ),
        ),
    ]
