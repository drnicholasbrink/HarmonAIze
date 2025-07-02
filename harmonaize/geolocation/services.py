from django.db.models import Q
from fuzzywuzzy import fuzz, process
from .models import ValidationDataset, LocationProcessingLog
from core.models import Location  # Import the core Location model

class LocationMatchingService:
    def __init__(self, fuzzy_threshold=85):
        self.fuzzy_threshold = fuzzy_threshold

    def process_locations_from_core(self, country_filter=None, batch_size=100):
        """Process locations from the core app and match them against the validation dataset."""
        locations_to_process = Location.objects.filter(latitude__isnull=True, longitude__isnull=True)

        if country_filter:
            locations_to_process = locations_to_process.filter(country__iexact=country_filter)

        results = {
            'processed': 0,
            'validation_matches': 0,
            'needs_geocoding': 0,
            'errors': []
        }

        for location in locations_to_process[:batch_size]:
            try:
                result = self.process_single_location(location)
                results['processed'] += 1

                if result['validation_match']:
                    results['validation_matches'] += 1
                else:
                    results['needs_geocoding'] += 1

            except Exception as e:
                results['errors'].append(f"Error processing {location.name}: {str(e)}")

        return results

    def process_single_location(self, core_location):
        """Process a single location through the matching pipeline."""
        # Create or get processing log
        log, created = LocationProcessingLog.objects.get_or_create(
            core_location=core_location,
            defaults={'original_name': core_location.name}
        )

        # Step 1: Check validation dataset
        validation_match = self.find_validation_match(core_location.name, core_location.country)

        if validation_match:
            # If a match is found, update the core location with validated coordinates
            core_location.latitude = validation_match.final_lat
            core_location.longitude = validation_match.final_long
            core_location.save()

            log.validation_match = validation_match
            log.processing_status = 'validation_matched'
            log.match_confidence = validation_match.confidence_score
            log.processing_notes = f"Matched with validation dataset: {validation_match.location_name}"
            log.save()

            return {'validation_match': validation_match}

        # Step 2: No matches found - needs geocoding
        log.processing_status = 'pending'
        log.processing_notes = "No matches found - needs geocoding"
        log.save()

        return {'validation_match': None}

    def find_validation_match(self, location_name, country_hint=None):
        """Find matching location in validation dataset."""
        validation_query = ValidationDataset.objects.all()
        if country_hint:
            validation_query = validation_query.filter(country__iexact=country_hint)

        # First, try exact match
        exact_match = validation_query.filter(
            Q(location_name__iexact=location_name)
        ).first()

        if exact_match:
            return exact_match

        # Try fuzzy matching if no exact match was found
        validation_names = list(validation_query.values_list('location_name', flat=True))
        best_match = process.extractOne(location_name, validation_names, scorer=fuzz.ratio)

        if best_match and best_match[1] >= self.fuzzy_threshold:
            matched_validation = validation_query.get(location_name=best_match[0])
            return matched_validation

        return None