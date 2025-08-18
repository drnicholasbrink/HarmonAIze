# geolocation/management/commands/test_validation.py
from django.core.management.base import BaseCommand
from geolocation.models import GeocodingResult, ValidationResult
from geolocation.validation import SmartGeocodingValidator, run_smart_validation
from core.models import Location

class Command(BaseCommand):
    help = 'Test the enhanced validation system with full workflow'

    def add_arguments(self, parser):
        parser.add_argument(
            '--run-geocoding',
            action='store_true',
            help='Run geocoding on sample locations first'
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=5,
            help='Limit the number of locations to test'
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("🤖 Testing Enhanced HarmonAIze Validation System"))
        self.stdout.write("=" * 60)
        
        # Step 1: Check current state
        total_locations = Location.objects.count()
        geocoded_locations = Location.objects.filter(
            latitude__isnull=False, 
            longitude__isnull=False
        ).count()
        geocoding_results = GeocodingResult.objects.count()
        validations = ValidationResult.objects.count()
        
        self.stdout.write(f"\n📊 Current System State:")
        self.stdout.write(f"   Total Locations: {total_locations}")
        self.stdout.write(f"   Geocoded Locations: {geocoded_locations}")
        self.stdout.write(f"   Geocoding Results: {geocoding_results}")
        self.stdout.write(f"   Validation Results: {validations}")
        
        # Step 2: Run geocoding if requested
        if options['run_geocoding']:
            self.stdout.write(f"\n🔍 Running Geocoding on {options['limit']} locations...")
            from geolocation.management.commands.geocode_locations import Command as GeocodeCommand
            
            geocode_cmd = GeocodeCommand()
            ungeocoded = Location.objects.filter(
                latitude__isnull=True, 
                longitude__isnull=True
            )[:options['limit']]
            
            if ungeocoded.exists():
                for location in ungeocoded:
                    # Check validated dataset first
                    validated_result = geocode_cmd.check_validated_dataset(location)
                    if validated_result:
                        location.latitude = validated_result.final_lat
                        location.longitude = validated_result.final_long
                        location.save()
                        self.stdout.write(f"   ✓ {location.name} - From validated dataset")
                    else:
                        # Geocode
                        success = geocode_cmd.geocode_location(location)
                        if success:
                            self.stdout.write(f"   ✓ {location.name} - Geocoded successfully")
                        else:
                            self.stdout.write(f"   ✗ {location.name} - Geocoding failed")
            else:
                self.stdout.write("   ℹ️  No locations need geocoding")
        
        # Step 3: Test Smart Validation
        self.stdout.write(f"\n🧠 Testing Smart Validation...")
        
        # Get geocoding results without validation
        pending_results = GeocodingResult.objects.filter(
            validation__isnull=True
        )[:options['limit']]
        
        if not pending_results.exists():
            self.stdout.write("   ℹ️  No pending geocoding results found")
            # Get any existing results for testing
            pending_results = GeocodingResult.objects.all()[:options['limit']]
        
        if pending_results.exists():
            validator = SmartGeocodingValidator()
            
            for result in pending_results:
                self.stdout.write(f"\n   🔬 Analyzing: {result.location_name}")
                
                try:
                    validation = validator.validate_geocoding_result(result)
                    
                    # Extract analysis details
                    metadata = validation.validation_metadata or {}
                    analysis = metadata.get('coordinates_analysis', {})
                    reverse_geocoding = metadata.get('reverse_geocoding_results', {})
                    
                    self.stdout.write(f"      🎯 AI Confidence: {validation.confidence_score:.1%}")
                    self.stdout.write(f"      📊 Status: {validation.validation_status}")
                    self.stdout.write(f"      🎖️  Recommended Source: {analysis.get('recommended_source', 'Unknown')}")
                    
                    # Show reverse geocoding results
                    if reverse_geocoding:
                        self.stdout.write(f"      🔄 Reverse Geocoding:")
                        for source, info in reverse_geocoding.items():
                            similarity = info.get('similarity_score', 0) * 100
                            address = info.get('address', 'No address')[:50]
                            self.stdout.write(f"         {source}: {similarity:.0f}% - {address}")
                    
                    # Show coordinate variance
                    variance = result.coordinate_variance or 0
                    if variance < 0.001:
                        precision = "Excellent (sources agree closely)"
                    elif variance < 0.01:
                        precision = "Good (minor variations)"
                    else:
                        precision = "Moderate (review needed)"
                    self.stdout.write(f"      📐 Coordinate Precision: {precision}")
                    
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f"      ✗ Validation failed: {str(e)}")
                    )
        
        # Step 4: Run Batch Validation
        self.stdout.write(f"\n⚡ Running Batch Smart Validation...")
        try:
            stats = run_smart_validation(limit=options['limit'])
            
            self.stdout.write(f"   📈 Batch Results:")
            self.stdout.write(f"      Processed: {stats['processed']}")
            self.stdout.write(f"      Auto-validated: {stats['auto_validated']}")
            self.stdout.write(f"      Needs Review: {stats['needs_review']}")
            self.stdout.write(f"      Pending Manual: {stats['pending']}")
            self.stdout.write(f"      Rejected: {stats['rejected']}")
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"   ✗ Batch validation failed: {str(e)}"))
        
        # Step 5: Show Final Statistics
        self.stdout.write(f"\n📊 Final System State:")
        
        # Updated counts
        final_geocoded = Location.objects.filter(
            latitude__isnull=False, 
            longitude__isnull=False
        ).count()
        final_geocoding_results = GeocodingResult.objects.count()
        final_validations = ValidationResult.objects.count()
        
        high_confidence = ValidationResult.objects.filter(confidence_score__gte=0.8).count()
        medium_confidence = ValidationResult.objects.filter(
            confidence_score__gte=0.6, confidence_score__lt=0.8
        ).count()
        low_confidence = ValidationResult.objects.filter(confidence_score__lt=0.6).count()
        
        self.stdout.write(f"   Total Locations: {total_locations}")
        self.stdout.write(f"   Geocoded Locations: {final_geocoded} (+{final_geocoded - geocoded_locations})")
        self.stdout.write(f"   Geocoding Results: {final_geocoding_results} (+{final_geocoding_results - geocoding_results})")
        self.stdout.write(f"   Validation Results: {final_validations} (+{final_validations - validations})")
        self.stdout.write(f"   High Confidence (80%+): {high_confidence}")
        self.stdout.write(f"   Medium Confidence (60-79%): {medium_confidence}")
        self.stdout.write(f"   Low Confidence (<60%): {low_confidence}")
        
        completion_rate = (final_geocoded / total_locations * 100) if total_locations > 0 else 0
        self.stdout.write(f"   Overall Completion: {completion_rate:.1f}%")
        
        # Step 6: Interface Information
        self.stdout.write(f"\n🌐 Access the Interface:")
        self.stdout.write(f"   Dashboard: http://localhost:8000/dashboard/")
        self.stdout.write(f"   Map Validator: http://localhost:8000/validation/")
        
        # Step 7: Usage Instructions
        self.stdout.write(f"\n📋 Next Steps:")
        self.stdout.write(f"   1. Visit the Dashboard to see real-time stats")
        self.stdout.write(f"   2. Run geocoding from the web interface")
        self.stdout.write(f"   3. Use AI validation for batch processing")
        self.stdout.write(f"   4. Use Map Validator for manual review")
        
        # Step 8: Show some sample validations
        sample_validations = ValidationResult.objects.select_related('geocoding_result').order_by('-confidence_score')[:3]
        
        if sample_validations.exists():
            self.stdout.write(f"\n🎯 Sample Validation Results:")
            for validation in sample_validations:
                status_emoji = "✅" if validation.validation_status == 'validated' else "⏳" if validation.validation_status == 'needs_review' else "❌"
                self.stdout.write(
                    f"   {status_emoji} {validation.geocoding_result.location_name}: "
                    f"{validation.confidence_score:.1%} confidence, "
                    f"{validation.validation_status}"
                )
        
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("🎉 HarmonAIze Validation System Test Complete!"))
        
        if final_validations > 0:
            self.stdout.write(self.style.SUCCESS(f"✅ System is working correctly with {final_validations} validations processed"))
        else:
            self.stdout.write(self.style.WARNING("⚠️  No validations were created. Try running with --run-geocoding"))
        
        self.stdout.write(f"\n🚀 Ready for production use!")
        self.stdout.write(f"   Visit: http://localhost:8000/dashboard/")