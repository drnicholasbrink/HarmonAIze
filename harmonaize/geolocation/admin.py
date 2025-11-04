
from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from .models import ValidatedDataset, HDXHealthFacility, GeocodingResult, ValidationResult
@admin.register(ValidatedDataset)
class ValidatedDatasetAdmin(admin.ModelAdmin):
    list_display = [
        'location_name', 'country', 'coordinates_display', 
        'source', 'validated_at'
    ]
    list_filter = ['source', 'country', 'validated_at']
    search_fields = ['location_name', 'country', 'city_town']
    ordering = ['-validated_at']
    readonly_fields = ['validated_at']
    
    fieldsets = (
        ('Location Information', {
            'fields': ('location_name', 'country', 'state_province', 'city_town')
        }),
        ('Address Details', {
            'fields': ('county', 'ward', 'suburb_village', 'street', 'house_number', 'postal_code'),
            'classes': ('collapse',)
        }),
        ('Coordinates', {
            'fields': ('final_lat', 'final_long', 'source')
        }),
        ('Metadata', {
            'fields': ('validated_at',),
            'classes': ('collapse',)
        })
    )
    
    def coordinates_display(self, obj):
        """Simple coordinates display."""
        if obj.final_lat and obj.final_long:
            return f"{obj.final_lat:.5f}, {obj.final_long:.5f}"
        return "No coordinates"
    coordinates_display.short_description = "Coordinates"
@admin.register(HDXHealthFacility)
class HDXHealthFacilityAdmin(admin.ModelAdmin):
    list_display = [
        'facility_name', 'facility_type', 'district', 'country', 
        'hdx_coordinates_display'
    ]
    list_filter = ['facility_type', 'ownership', 'country', 'province']
    search_fields = ['facility_name', 'district', 'city', 'country']
    ordering = ['country', 'province', 'district', 'facility_name']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Facility Information', {
            'fields': ('facility_name', 'facility_type', 'ownership', 'source')
        }),
        ('Location', {
            'fields': ('country', 'province', 'city', 'district', 'ward')
        }),
        ('Coordinates', {
            'fields': ('hdx_latitude', 'hdx_longitude')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def hdx_coordinates_display(self, obj):
        """Simple HDX coordinates display."""
        if obj.hdx_latitude and obj.hdx_longitude:
            return f"{obj.hdx_latitude:.5f}, {obj.hdx_longitude:.5f}"
        return "No coordinates"
    hdx_coordinates_display.short_description = "HDX Coordinates"
@admin.register(GeocodingResult)
class GeocodingResultAdmin(admin.ModelAdmin):
    list_display = [
        'location_name', 'validation_status', 'successful_sources_display', 
        'coordinate_variance', 'created_at', 'view_validation'
    ]
    list_filter = ['validation_status', 'created_at', 'arcgis_success', 'google_success', 'nominatim_success', 'hdx_success']
    search_fields = ['location_name', 'notes']
    ordering = ['-created_at']
    readonly_fields = ['created_at', 'validated_at', 'successful_sources_display', 'results_summary_display', 'view_validation']
    
    fieldsets = (
        ('Location', {
            'fields': ('location_name', 'validation_status', 'coordinate_variance')
        }),
        ('ArcGIS Results', {
            'fields': ('arcgis_success', 'arcgis_lat', 'arcgis_lng', 'arcgis_error'),
            'classes': ('collapse',)
        }),
        ('Google Results', {
            'fields': ('google_success', 'google_lat', 'google_lng', 'google_error'),
            'classes': ('collapse',)
        }),
        ('Nominatim Results', {
            'fields': ('nominatim_success', 'nominatim_lat', 'nominatim_lng', 'nominatim_error'),
            'classes': ('collapse',)
        }),
        ('HDX Results', {
            'fields': ('hdx_success', 'hdx_lat', 'hdx_lng', 'hdx_error', 'hdx_facility_match'),
            'classes': ('collapse',)
        }),
        ('Validation', {
            'fields': ('selected_source', 'final_lat', 'final_lng', 'notes', 'validated_by', 'validated_at')
        }),
        ('Summary', {
            'fields': ('successful_sources_display', 'results_summary_display', 'view_validation')
        }),
        ('Metadata', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        })
    )
    
    def successful_sources_display(self, obj):
        """Safe display of successful sources."""
        try:
            sources = obj.successful_apis
            if sources:
                colours = {
                    'hdx': '#2563eb',
                    'arcgis': '#8b5cf6',  # Purple instead of green
                    'google': '#dc2626',
                    'nominatim': '#d97706'
                }
                badges = []
                for source in sources:
                    colour = colours.get(source, '#6b7280')
                    badge_html = '<span style="background-colour: {}; colour: white; padding: 2px 6px; border-radius: 4px; font-size: 11px; margin-right: 4px;">{}</span>'.format(
                        colour, str(source).upper()
                    )
                    badges.append(badge_html)
                return mark_safe(''.join(badges))
            return "None"
        except Exception as e:
            return "Error loading sources"
    successful_sources_display.short_description = "Successful Sources"
    
    def results_summary_display(self, obj):
        """Safe display of coordinates summary."""
        try:
            summary = obj.results_summary
            if summary:
                display = []
                for source, coords in summary.items():
                    if coords and 'lat' in coords and 'lng' in coords:
                        coord_str = "{}: {:.5f}, {:.5f}".format(
                            str(source).upper(), 
                            float(coords['lat']), 
                            float(coords['lng'])
                        )
                        display.append(coord_str)
                return mark_safe('<br>'.join(display))
            return "No results"
        except Exception as e:
            return "Error loading coordinates"
    results_summary_display.short_description = "Coordinates Summary"
    
    def view_validation(self, obj):
        """Safe validation link."""
        try:
            if hasattr(obj, 'validation'):
                validation_url = reverse('admin:geolocation_validationresult_change', args=[obj.validation.id])
                return format_html('<a href="{}">View Validation</a>', validation_url)
            return "No validation"
        except Exception as e:
            return "Error loading validation"
    view_validation.short_description = "Validation"
@admin.register(ValidationResult)
class ValidationResultAdmin(admin.ModelAdmin):
    list_display = [
        'geocoding_result', 'confidence_score', 'validation_status', 
        'recommended_source', 'confidence_level_display', 'validated_at'
    ]
    list_filter = ['validation_status', 'recommended_source', 'validated_at', 'created_at']
    search_fields = ['geocoding_result__location_name', 'manual_review_notes', 'validated_by']
    ordering = ['-confidence_score', '-created_at']
    readonly_fields = [
        'created_at', 'updated_at', 'confidence_level_display', 
        'metadata_summary', 'view_geocoding_result'
    ]
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('geocoding_result', 'validation_status', 'view_geocoding_result')
        }),
        ('AI Analysis Scores', {
            'fields': (
                'confidence_score', 'confidence_level_display',
                'api_agreement_score', 'reverse_geocoding_score', 
                'distance_confidence', 'source_reliability_score'
            )
        }),
        ('AI Recommendations', {
            'fields': ('recommended_source', 'recommended_lat', 'recommended_lng')
        }),
        ('Manual Review', {
            'fields': ('manual_review_notes', 'manual_lat', 'manual_lng'),
            'classes': ('collapse',)
        }),
        ('Validation Details', {
            'fields': ('validated_by', 'validated_at', 'metadata_summary'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def confidence_level_display(self, obj):
        """Safe display of confidence level without f-string formatting on SafeString."""
        try:
            level = obj.confidence_level
            score = obj.confidence_score * 100
            colours = {
                'High': '#22c55e',
                'Medium': '#f59e0b', 
                'Low': '#ef4444'
            }
            colour = colours.get(level, '#6b7280')
            

            return format_html(
                '<span style="background-colour: {}; colour: white; padding: 4px 8px; border-radius: 6px; font-weight: bold;">{} ({:.0f}%)</span>',
                colour, level, score
            )
        except Exception as e:
            return "Error loading confidence"
    confidence_level_display.short_description = "Confidence Level"
    
    def metadata_summary(self, obj):
        """Safe display of metadata summary."""
        try:
            if obj.validation_metadata:
                summary = obj.validation_metadata.get('user_friendly_summary', 'No summary available')

                summary_str = str(summary)
                return format_html('<div style="max-width: 400px;">{}</div>', summary_str)
            return "No metadata"
        except Exception as e:
            return "Error loading metadata"
    metadata_summary.short_description = "AI Analysis Summary"
    
    def view_geocoding_result(self, obj):
        """Safe link to geocoding result."""
        try:
            geocoding_url = reverse('admin:geolocation_geocodingresult_change', args=[obj.geocoding_result.id])
            return format_html('<a href="{}">View Geocoding Result</a>', geocoding_url)
        except Exception as e:
            return "Error loading link"
    view_geocoding_result.short_description = "Geocoding Result"
    

admin.site.site_header = "HarmonAIze Geolocation Admin"
admin.site.site_title = "Geolocation Admin"
admin.site.index_title = "Geolocation Management"