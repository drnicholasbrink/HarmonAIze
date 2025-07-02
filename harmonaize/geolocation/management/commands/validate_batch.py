# geolocation/management/commands/validate_batch.py
# Command to run batch AI validation on geocoding results

from django.core.management.base import BaseCommand
from django.db import transaction
from geolocation.models import GeocodingResult, ValidationResult
from geolocation.validation import SmartGeocodingValidator
from core.models import Location


class Command(BaseCommand):
    help = 'Run AI validation on geocoding results in batches'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=50,
            help='Number of results to validate per batch (default: 50)'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Re-validate already validated results'
        )
        parser.add_argument(
            '--confidence-threshold',
            type=float,
            default=0.8,
            help='Confidence threshold for auto-validation (default: 0.8)'
        )
        parser.add_argument(
            '--auto-validate',
            action='store_true',
            help='Automatically validate high-confidence results'
        )
        parser.add_argument(
            '--location-name',
            type=str,
            help='Validate specific location by name'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be validated without actually validating'
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting HarmonAIze AI validation..."))

        # Initialize validator
        validator = SmartGeocodingValidator()
        
        # Get geocoding results to validate
        if options['location_name']:
            geocoding_results = GeocodingResult.objects.filter(
                location_name__icontains=options['location_name']
            )
        elif options['force']:
            geocoding_results = GeocodingResult.objects.all()
        else:
            # Get results without validation
            geocoding_results = GeocodingResult.objects.filter(
                validation__isnull=True
            ).exclude(validation_status='rejected')

        if options['limit']:
            geocoding_results = geocoding_results[:options['limit']]

        if not geocoding_results.exists():
            self.stdout.write(self.style.WARNING("No geocoding results found for validation."))
            return

        total_count = geocoding_results.count()
        self.stdout.write(f"Found {total_count} geocoding results to validate")

        if options['dry_run']:
            self.stdout.write(self.style.WARNING("DRY RUN - No validations will be saved"))
            self.show_dry_run_preview(geocoding_results[:5])
            return

        # Process results
        stats = {
            'processed': 0,
            'auto_validated': 0,
            'needs_review': 0,
            'pending': 0,
            'rejected': 0,
            'errors': 0
        }

        for i, result in enumerate(geocoding_results, 1):
            try:
                self.stdout.write(f"[{i}/{total_count}] Validating: {result.location_name}")
                
                # Check if already has validation
                if hasattr(result, 'validation') and not options['force']:
                    self.stdout.write(f"  ⚠ Already validated - skipping")
                    continue

                # Run AI validation
                validation = validator.validate_geocoding_result(result)
                stats['processed'] += 1

                # Map validation status to stats
                if validation.validation_status == 'validated':
                    stats['auto_validated'] += 1
                    self.stdout.write(f"  ✅ Auto-validated (confidence: {validation.confidence_score:.2%})")
                elif validation.validation_status == 'needs_review':
                    stats['needs_review'] += 1
                    self.stdout.write(f"  ⚠ Needs review (confidence: {validation.confidence_score:.2%})")
                elif validation.validation_status == 'pending':
                    stats['pending'] += 1
                    self.stdout.write(f"  ⏳ Pending manual review (confidence: {validation.confidence_score:.2%})")
                else:
                    stats['rejected'] += 1
                    self.stdout.write(f"  ❌ Rejected (confidence: {validation.confidence_score:.2%})")

                # Auto-validate high confidence results if requested
                if (options['auto_validate'] and 
                    validation.confidence_score >= options['confidence_threshold'] and
                    validation.validation_status == 'needs_review'):
                    
                    self.auto_validate_result(validation)
                    stats['auto_validated'] += 1
                    stats['needs_review'] -= 1
                    self.stdout.write(f"  🚀 Auto-approved high confidence result")

                # Progress indicator
                if i % 10 == 0:
                    self.stdout.write(f"Progress: {i}/{total_count} completed")

            except Exception as e:
                stats['errors'] += 1
                self.stdout.write(
                    self.style.ERROR(f"  ✗ Error validating {result.location_name}: {str(e)}")
                )
                continue

        # Final summary
        self.stdout.write(
            self.style.SUCCESS(
                f"\nAI Validation completed:\n"
                f"  📊 Total processed: {stats['processed']}\n"
                f"  ✅ Auto-validated: {stats['auto_validated']}\n"
                f"  ⚠ Needs review: {stats['needs_review']}\n"
                f"  ⏳ Pending manual: {stats['pending']}\n"
                f"  ❌ Rejected: {stats['rejected']}\n"
                f"  ✗ Errors: {stats['errors']}"
            )
        )

        # Show next steps
        if stats['needs_review'] > 0:
            self.stdout.write(
                self.style.WARNING(
                    f"\n💡 Next steps:\n"
                    f"  • {stats['needs_review']} locations need human review\n"
                    f"  • Use the validation map interface to review them\n"
                    f"  • Run with --auto-validate to approve high-confidence results automatically"
                )
            )

    def show_dry_run_preview(self, results):
        """Show preview of what would be validated in dry run."""
        self.stdout.write("\nDRY RUN PREVIEW (first 5 results):")
        
        validator = SmartGeocodingValidator()
        
        for result in results:
            self.stdout.write(f"\n📍 {result.location_name}")
            
            # Show available sources
            sources = result.successful_apis
            if sources:
                self.stdout.write(f"  Sources: {', '.join(sources)}")
                
                # Quick validation preview (without saving)
                coordinates = validator._extract_coordinates(result)
                if coordinates:
                    self.stdout.write(f"  Coordinates found: {len(coordinates)} sources")
                    for source, (lat, lng) in coordinates.items():
                        self.stdout.write(f"    {source.upper()}: {lat:.5f}, {lng:.5f}")
                else:
                    self.stdout.write("  ❌ No valid coordinates found")
            else:
                self.stdout.write("  ❌ No successful geocoding results")

    def auto_validate_result(self, validation):
        """Auto-validate a high-confidence result."""
        try:
            # Get AI recommended source from metadata
            metadata = validation.validation_metadata or {}
            analysis = metadata.get('coordinates_analysis', {})
            recommended_source = analysis.get('recommended_source')
            
            if not recommended_source:
                return False

            with transaction.atomic():
                result = validation.geocoding_result
                
                # Get coordinates from AI recommended source
                if recommended_source == 'hdx' and result.hdx_success:
                    final_lat, final_lng = result.hdx_lat, result.hdx_lng
                elif recommended_source == 'arcgis' and result.arcgis_success:
                    final_lat, final_lng = result.arcgis_lat, result.arcgis_lng
                elif recommended_source == 'google' and result.google_success:
                    final_lat, final_lng = result.google_lat, result.google_lng
                elif recommended_source == 'nominatim' and result.nominatim_success:
                    final_lat, final_lng = result.nominatim_lat, result.nominatim_lng
                else:
                    return False
                
                # Update validation status
                validation.validation_status = 'validated'
                validation.validated_by = 'Auto_Batch_Validation'
                validation.recommended_lat = final_lat
                validation.recommended_lng = final_lng
                validation.recommended_source = recommended_source
                validation.save()
                
                # Update the core Location model
                try:
                    location = Location.objects.get(name__iexact=result.location_name)
                    location.latitude = final_lat
                    location.longitude = final_lng
                    location.save()
                except Location.DoesNotExist:
                    pass  # Location might not exist in core model
                except Location.MultipleObjectsReturned:
                    # Update the first match if multiple exist
                    location = Location.objects.filter(name__iexact=result.location_name).first()
                    location.latitude = final_lat
                    location.longitude = final_lng
                    location.save()
                
                return True

        except Exception as e:
            self.stdout.write(f"Error auto-validating: {str(e)}")
            return False