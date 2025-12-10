# Generated migration to add category choices to ClimateVariable model

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('climate', '0003_make_category_field_flexible'),
    ]

    operations = [
        migrations.AlterField(
            model_name='climatevariable',
            name='category',
            field=models.CharField(
                choices=[
                    ('temperature', 'Temperature'),
                    ('precipitation', 'Precipitation'),
                    ('humidity', 'Humidity & Moisture'),
                    ('wind', 'Wind'),
                    ('pressure', 'Atmospheric Pressure'),
                    ('radiation', 'Solar Radiation'),
                    ('evapotranspiration', 'Evapotranspiration'),
                    ('air_quality', 'Air Quality'),
                    ('vegetation', 'Vegetation Indices'),
                    ('cloud_cover', 'Cloud Cover'),
                    ('extreme_events', 'Extreme Events'),
                    ('other', 'Other'),
                ],
                default='other',
                help_text='Variable category for organizing climate variables. Select from predefined categories to ensure API compatibility.',
                max_length=100
            ),
        ),
    ]
