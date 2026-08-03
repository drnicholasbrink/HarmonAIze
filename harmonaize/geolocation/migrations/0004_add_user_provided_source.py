from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('geolocation', '0003_add_admin_boundary_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='geocodingresult',
            name='user_provided_lat',
            field=models.FloatField(
                blank=True,
                null=True,
                help_text='Latitude provided directly in the uploaded CSV file',
            ),
        ),
        migrations.AddField(
            model_name='geocodingresult',
            name='user_provided_lng',
            field=models.FloatField(
                blank=True,
                null=True,
                help_text='Longitude provided directly in the uploaded CSV file',
            ),
        ),
        migrations.AddField(
            model_name='geocodingresult',
            name='user_provided_success',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='geocodingresult',
            name='user_provided_error',
            field=models.TextField(blank=True),
        ),
    ]
