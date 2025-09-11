
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
 
        try:
            
            pass
        except ImportError:
            pass