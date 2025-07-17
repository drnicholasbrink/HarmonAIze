# geolocation/apps.py
from django.apps import AppConfig


class GeolocationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'geolocation'
    verbose_name = 'Geolocation & Validation'
    
    def ready(self):
        """
        Perform initialization tasks when the app is ready.
        This method is called once Django has loaded all models.
        """
        # Import any signal handlers or perform startup tasks here
        try:
            # You can import signals here if you have any
            # from . import signals
            pass
        except ImportError:
            pass