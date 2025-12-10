# Data migration to map existing custom categories to predefined choices

from django.db import migrations


def map_existing_categories(apps, schema_editor):
    """
    Map any existing custom categories to the predefined category choices.
    """
    ClimateVariable = apps.get_model('climate', 'ClimateVariable')

    # Mapping of possible custom category values to standard categories
    category_mapping = {
        'temp': 'temperature',
        'temps': 'temperature',
        'precip': 'precipitation',
        'rain': 'precipitation',
        'rainfall': 'precipitation',
        'humid': 'humidity',
        'moisture': 'humidity',
        'winds': 'wind',
        'atm_pressure': 'pressure',
        'solar': 'radiation',
        'et': 'evapotranspiration',
        'evaporation': 'evapotranspiration',
        'air': 'air_quality',
        'ndvi': 'vegetation',
        'evi': 'vegetation',
        'clouds': 'cloud_cover',
        'extreme': 'extreme_events',
    }

    for variable in ClimateVariable.objects.all():
        if variable.category:
            # Check if category is already a valid choice
            valid_categories = [
                'temperature', 'precipitation', 'humidity', 'wind', 'pressure',
                'radiation', 'evapotranspiration', 'air_quality', 'vegetation',
                'cloud_cover', 'extreme_events', 'other'
            ]

            if variable.category.lower() not in valid_categories:
                # Try to map it
                mapped_category = category_mapping.get(variable.category.lower(), 'other')
                variable.category = mapped_category
                variable.save()
        else:
            # Set default for empty categories
            variable.category = 'other'
            variable.save()


def reverse_migration(apps, schema_editor):
    """
    Reverse migration - no changes needed as we're just standardizing values.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('climate', '0004_add_category_choices'),
    ]

    operations = [
        migrations.RunPython(map_existing_categories, reverse_migration),
    ]
