
from django.apps import AppConfig
class GeolocationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'harmonaize.geolocation'
    verbose_name = 'Geolocation & Validation'
    
    def ready(self):
        """
        Perform initialization tasks when the app is ready.
        This method is called once Django has loaded all models.
        """
        try:
            self.load_initial_data()
        except ImportError:
            pass
    
    def load_initial_data(self):
        """
        Automatically load geolocation CSV data if files exist and database is empty.
        Only runs once when database is first set up.
        """
        import os
        from django.core.management import call_command
        from django.db import connection
        from .models import HDXHealthFacility, ValidatedDataset

        try:
            if 'migrate' not in connection.queries:
                hdx_file = 'data_geocoding/2025_Health Africa.csv'
                if os.path.exists(hdx_file) and not HDXHealthFacility.objects.exists():
                    call_command('load_hdx_data', file=hdx_file)

                validation_file = 'data_geocoding/Validated_locations.csv'
                if os.path.exists(validation_file) and not ValidatedDataset.objects.exists():
                    call_command('load_validation_data', validation_file)
        except Exception:
            pass

