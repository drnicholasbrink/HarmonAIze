# Generated manually to remove country field
from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_location_country'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='location',
            name='country',
        ),
    ]