# Generated migration for adding parsed_location_data field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('geolocation', '0009_rename_validationdataset_to_validateddataset'),
    ]

    operations = [
        migrations.AddField(
            model_name='geocodingresult',
            name='parsed_location_data',
            field=models.JSONField(
                blank=True,
                help_text='Parsed location components (country, city, facility) from intelligent parsing',
                null=True
            ),
        ),
    ]
