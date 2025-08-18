# geolocation/admin.py
from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from .models import ValidationDataset, HDXHealthFacility, GeocodingResult, ValidationResult


@admin.register(ValidationDataset)
class ValidationDatasetAdmin(admin.ModelAdmin):
    list_display = [
        'location_name', 'country', 'final_lat', 'final_long', 
        'source', 'validated_at', 'view_on_map'
    ]
    list_filter = ['source', 'country', 'validated_at']
    search_fields = ['location_name', 'country', 'city_town']
    ordering = ['-validated_at']
    readonly_fields = ['validated_at', 'view_on_map']
    
    fieldsets = (
        ('Location Information', {
            'fields': ('location_name', 'country', 'state_province', 'city_town')
        }),
        ('Address Details', {
            'fields': ('county', 'ward', 'suburb_village', 'street', 'house_number', 'postal_code'),
            'classes': ('collapse',)
        }),
        ('Coordinates', {
            'fields': ('final_lat', 'final_long', 'source', 'view_on_map')
        }),
        ('Metadata', {
            'fields': ('validated_at',),
            'classes': ('collapse',)
        })
    )
    
    def view_on_map(self, obj):
        """Safe map link generation for ValidationDataset."""
        try:
            if obj.final_lat and obj.final_long:
                lat_str = "{:.5f}".format(float(obj.final_lat))
                lng_str = "{:.5f}".format(float(obj.final_long))
                return format_html(
                    '<a href="https://www.google.com/maps/@{},{},15z" target="_blank">📍 View on Map</a>',
                    lat_str, lng_str
                )
            return "No coordinates"
        except (ValueError, TypeError):
            return "Invalid coordinates"
    view_on_map.short_description = "Map Link"


@admin.register(HDXHealthFacility)
class HDXHealthFacilityAdmin(admin.ModelAdmin):
    list_display = [
        'facility_name', 'facility_type', 'district', 'country', 
        'hdx_latitude', 'hdx_longitude', 'view_on_map'
    ]
    list_filter = ['facility_type', 'ownership', 'country', 'province']
    search_fields = ['facility_name', 'district', 'city', 'country']
    ordering = ['country', 'province', 'district', 'facility_name']
    readonly_fields = ['created_at', 'updated_at', 'view_on_map']
    
    fieldsets = (
        ('Facility Information', {
            'fields': ('facility_name', 'facility_type', 'ownership', 'source')
        }),
        ('Location', {
            'fields': ('country', 'province', 'city', 'district', 'ward')
        }),
        ('Coordinates', {
            'fields': ('hdx_latitude', 'hdx_longitude', 'view_on_map')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def view_on_map(self, obj):
        """Safe map link generation for HDXHealthFacility."""
        try:
            if obj.hdx_latitude and obj.hdx_longitude:
                lat_str = "{:.5f}".format(float(obj.hdx_latitude))
                lng_str = "{:.5f}".format(float(obj.hdx_longitude))
                return format_html(
                    '<a href="https://www.google.com/maps/@{},{},15z" target="_blank">📍 View on Map</a>',
                    lat_str, lng_str
                )
            return "No coordinates"
        except (ValueError, TypeError):
            return "Invalid coordinates"
    view_on_map.short_description = "Map Link"


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
                colors = {
                    'hdx': '#2563eb',
                    'arcgis': '#8b5cf6',  # Purple instead of green
                    'google': '#dc2626',
                    'nominatim': '#d97706'
                }
                badges = []
                for source in sources:
                    color = colors.get(source, '#6b7280')
                    badge_html = '<span style="background-color: {}; color: white; padding: 2px 6px; border-radius: 4px; font-size: 11px; margin-right: 4px;">{}</span>'.format(
                        color, str(source).upper()
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
                return format_html('<a href="{}">📊 View Validation</a>', validation_url)
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
        'metadata_summary', 'view_geocoding_result', 'view_on_map'
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
            'fields': ('recommended_source', 'recommended_lat', 'recommended_lng', 'view_on_map')
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
            colors = {
                'High': '#22c55e',
                'Medium': '#f59e0b', 
                'Low': '#ef4444'
            }
            color = colors.get(level, '#6b7280')
            
            # Use .format() method instead of f-strings to avoid SafeString issues
            return format_html(
                '<span style="background-color: {}; color: white; padding: 4px 8px; border-radius: 6px; font-weight: bold;">{} ({:.0f}%)</span>',
                color, level, score
            )
        except Exception as e:
            return "Error loading confidence"
    confidence_level_display.short_description = "Confidence Level"
    
    def metadata_summary(self, obj):
        """Safe display of metadata summary."""
        try:
            if obj.validation_metadata:
                summary = obj.validation_metadata.get('user_friendly_summary', 'No summary available')
                # Ensure summary is a string before formatting
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
            return format_html('<a href="{}">🔍 View Geocoding Result</a>', geocoding_url)
        except Exception as e:
            return "Error loading link"
    view_geocoding_result.short_description = "Geocoding Result"
    
    def view_on_map(self, obj):
        """Safe map link generation."""
        try:
            coords = obj.final_coordinates
            if coords:
                lat, lng = coords
                # Ensure coordinates are properly formatted as numbers
                lat_str = "{:.5f}".format(float(lat))
                lng_str = "{:.5f}".format(float(lng))
                map_url = "https://www.google.com/maps/@{},{},15z".format(lat_str, lng_str)
                return format_html(
                    '<a href="{}" target="_blank">📍 View on Map</a>',
                    map_url
                )
            return "No coordinates"
        except Exception as e:
            return "Coordinate error"
    view_on_map.short_description = "Map Link"


# Custom admin site configuration
admin.site.site_header = "HarmonAIze Geolocation Admin"
admin.site.site_title = "Geolocation Admin"
admin.site.index_title = "Geolocation Management"