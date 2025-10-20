# Generated migration for renaming ValidationDataset to ValidatedDataset

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('geolocation', '0008_alter_geocodingresult_table_and_more'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='ValidationDataset',
            new_name='ValidatedDataset',
        ),
        migrations.AlterModelOptions(
            name='validateddataset',
            options={
                'verbose_name': 'Validated Location',
                'verbose_name_plural': 'Validated Locations (POI Arsenal)',
            },
        ),
    ]
