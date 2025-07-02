# geolocation/management/commands/process_locations.py
# Complete pipeline: geocode -> validate -> update datasets

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from geolocation.validation import run_smart_validation, process_location_batch
from geolocation.models import GeocodingResult, ValidationResult, ValidationDataset
from core.models import Location


class Command(BaseCommand):
    help = 'Complete location processing pipeline: geocode locations, run validation, update datasets'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=50,
            help='Number of locations to process in this batch'
        )
        parser.add_argument(
            '--force-geocode',
            action='store_true',
            help='Force re-geocoding even if results already exist'
        )
        parser.add_argument(
            '--skip-validation',
            action='store_true',
            help='Skip the validation step'
        )
        parser.add_argument(
            '--validate-only',
            action='store_true',
            help='Only run validation on existing geocoding results'
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        force_geocode = options['force_geocode']
        skip_validation = options['skip_validation']
        validate_only = options['validate_only']

        self.stdout.write(self.style.SUCCESS("🚀 Starting Location Processing Pipeline"))
        self.stdout.write("=" * 60)

        # Get initial stats
        initial_stats = self._get_pipeline_stats()
        self._display_initial_stats(initial_stats)

        if validate_only:
            # Only run validation
            self.stdout.write(self.style.WARNING("📊 Running validation only..."))
            validation_stats = run_smart_validation(limit=batch_size)
            self._display_validation_results(validation_stats)
        else:
            # Run complete pipeline
            pipeline_stats = process_location_batch(batch_size)
            self._display_pipeline_results(pipeline_stats)

            if not skip_validation:
                self.stdout.write(self.style.WARNING("📊 Running smart validation..."))
                validation_stats = run_smart_validation(limit=batch_size)
                self._display_validation_results(validation_stats)

        # Final summary
        final_stats = self._get_pipeline_stats()
        self._display_final_summary(initial_stats, final_stats)

    def _get_pipeline_stats(self):
        """Get current pipeline statistics."""
        return {
            'ungeocoded_locations': Location.objects.filter(
                latitude__isnull=True, longitude__isnull=True
            ).count(),
            'geocoded_locations': Location.objects.filter(
                latitude__isnull=False, longitude__isnull=False
            ).count(),
            'total_locations': Location.objects.count(),
            'geocoding_results': GeocodingResult.objects.count(),
            'pending_validation': GeocodingResult.objects.filter(
                validation__isnull=True
            ).count(),
            'validated_results': ValidationResult.objects.filter(
                validation_status='validated'
            ).count(),
            'needs_review': ValidationResult.objects.filter(
                validation_status='needs_review'
            ).count(),
            'manual_review': ValidationResult.objects.filter(
                validation_status='pending'
            ).count(),
            'validated_dataset_size': ValidationDataset.objects.count(),
        }

    def _display_initial_stats(self, stats):
        """Display initial pipeline statistics."""
        self.stdout.write("\n📊 Current Pipeline Status:")
        self.stdout.write("-" * 30)
        
        total = stats['total_locations']
        geocoded = stats['geocoded_locations']
        ungeocoded = stats['ungeocoded_locations']
        
        if total > 0:
            geocoded_pct = (geocoded / total) * 100
            self.stdout.write(f"📍 Locations: {total:,} total")
            self.stdout.write(f"   ✅ Geocoded: {geocoded:,} ({geocoded_pct:.1f}%)")
            self.stdout.write(f"   📌 Need geocoding: {ungeocoded:,}")
        
        self.stdout.write(f"🔍 Geocoding results: {stats['geocoding_results']:,}")
        self.stdout.write(f"⏳ Pending validation: {stats['pending_validation']:,}")
        self.stdout.write(f"✅ Validated: {stats['validated_results']:,}")
        self.stdout.write(f"⚠️ Need review: {stats['needs_review']:,}")
        self.stdout.write(f"🚨 Manual review: {stats['manual_review']:,}")
        self.stdout.write(f"💾 Validated dataset: {stats['validated_dataset_size']:,} entries")

    def _display_pipeline_results(self, stats):
        """Display geocoding pipeline results."""
        self.stdout.write("\n🔄 Geocoding Results:")
        self.stdout.write("-" * 25)
        self.stdout.write(f"📍 Locations processed: {stats['locations_processed']}")
        self.stdout.write(f"✅ Successfully geocoded: {stats['geocoding_successful']}")
        self.stdout.write(f"🎯 Auto-validated: {stats['auto_validated']}")
        
        if stats['locations_processed'] > 0:
            success_rate = (stats['geocoding_successful'] / stats['locations_processed']) * 100
            self.stdout.write(f"📊 Success rate: {success_rate:.1f}%")

    def _display_validation_results(self, stats):
        """Display validation results."""
        self.stdout.write("\n🤖 Smart Validation Results:")
        self.stdout.write("-" * 30)
        self.stdout.write(f"🔍 Results analyzed: {stats['processed']}")
        self.stdout.write(f"✅ Auto-validated: {stats['auto_validated']}")
        self.stdout.write(f"⚠️ Need review: {stats['needs_review']}")
        self.stdout.write(f"🚨 Manual review: {stats['pending']}")
        self.stdout.write(f"❌ Rejected: {stats['rejected']}")
        
        if stats['processed'] > 0:
            auto_rate = (stats['auto_validated'] / stats['processed']) * 100
            self.stdout.write(f"🎯 Auto-validation rate: {auto_rate:.1f}%")

    def _display_final_summary(self, initial_stats, final_stats):
        """Display final pipeline summary."""
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("📈 PIPELINE SUMMARY"))
        self.stdout.write("=" * 60)
        
        # Calculate changes
        locations_geocoded = final_stats['geocoded_locations'] - initial_stats['geocoded_locations']
        validations_created = final_stats['validated_results'] - initial_stats['validated_results']
        dataset_growth = final_stats['validated_dataset_size'] - initial_stats['validated_dataset_size']
        
        self.stdout.write(f"🎯 New locations geocoded: {locations_geocoded}")
        self.stdout.write(f"✅ New validations completed: {validations_created}")
        self.stdout.write(f"💾 Validated dataset growth: +{dataset_growth} entries")
        
        # Current status
        remaining = final_stats['ungeocoded_locations']
        pending_validation = final_stats['pending_validation']
        needs_attention = final_stats['needs_review'] + final_stats['manual_review']
        
        self.stdout.write(f"\n📊 Current Status:")
        self.stdout.write(f"   📌 Locations still need geocoding: {remaining:,}")
        self.stdout.write(f"   ⏳ Results need validation: {pending_validation:,}")
        self.stdout.write(f"   👀 Locations need human review: {needs_attention:,}")
        
        # Next steps
        self.stdout.write(f"\n💡 Next Steps:")
        if remaining > 0:
            self.stdout.write(f"   🔄 Run pipeline again to geocode {min(remaining, 50)} more locations")
        if pending_validation > 0:
            self.stdout.write(f"   🤖 Run validation: python manage.py run_smart_validation")
        if needs_attention > 0:
            self.stdout.write(f"   👀 Review {needs_attention} locations in validation interface")
        
        if remaining == 0 and pending_validation == 0 and needs_attention == 0:
            self.stdout.write(self.style.SUCCESS("   🎉 All locations processed! Pipeline complete."))
        
        # Performance tips
        if locations_geocoded > 0:
            self.stdout.write(f"\n💡 Performance:")
            self.stdout.write(f"   ⚡ Processed {locations_geocoded} locations in this run")
            if remaining > 100:
                self.stdout.write(f"   🔧 Consider increasing --batch-size for faster processing")
            if needs_attention > 20:
                self.stdout.write(f"   👥 Consider having multiple reviewers for validation")


# Additional utility command for quick status checks
class StatusCommand(BaseCommand):
    """Quick status check command"""
    help = 'Show current pipeline status'

    def handle(self, *args, **options):
        # Get stats
        ungeocoded = Location.objects.filter(
            latitude__isnull=True, longitude__isnull=True
        ).count()
        
        geocoded = Location.objects.filter(
            latitude__isnull=False, longitude__isnull=False
        ).count()
        
        pending_validation = GeocodingResult.objects.filter(
            validation__isnull=True
        ).count()
        
        needs_review = ValidationResult.objects.filter(
            validation_status__in=['needs_review', 'pending']
        ).count()
        
        validated_dataset = ValidationDataset.objects.count()
        
        self.stdout.write(self.style.SUCCESS("📊 Pipeline Status"))
        self.stdout.write("-" * 20)
        self.stdout.write(f"📍 Ungeocoded locations: {ungeocoded:,}")
        self.stdout.write(f"✅ Geocoded locations: {geocoded:,}")
        self.stdout.write(f"⏳ Pending validation: {pending_validation:,}")
        self.stdout.write(f"👀 Need human review: {needs_review:,}")
        self.stdout.write(f"💾 Validated dataset: {validated_dataset:,}")
        
        # Quick recommendations
        if ungeocoded > 0:
            self.stdout.write(f"\n💡 Next: python manage.py process_locations --batch-size {min(ungeocoded, 50)}")
        elif pending_validation > 0:
            self.stdout.write(f"\n💡 Next: python manage.py run_smart_validation")
        elif needs_review > 0:
            self.stdout.write(f"\n💡 Next: Review locations in validation interface")
        else:
            self.stdout.write(self.style.SUCCESS("\n🎉 All locations processed!"))