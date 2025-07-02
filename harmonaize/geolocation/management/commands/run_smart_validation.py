# geolocation/management/commands/run_smart_validation.py
# Django management command to run validation

from django.core.management.base import BaseCommand
from django.db import transaction
from geolocation.validation import SmartGeocodingValidator, run_smart_validation
from geolocation.models import GeocodingResult, ValidationResult


class Command(BaseCommand):
    help = 'Run smart validation on geocoding results with AI-assisted analysis'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit the number of results to process'
        )
        parser.add_argument(
            '--show-details',
            action='store_true',
            help='Show detailed analysis for each result'
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("🤖 Starting Smart Geocoding Validation..."))

        # Get results to validate
        queryset = GeocodingResult.objects.filter(
            validation__isnull=True
        ).exclude(validation_status='rejected')

        if options['limit']:
            queryset = queryset[:options['limit']]

        if not queryset.exists():
            self.stdout.write(self.style.WARNING("No geocoding results need validation."))
            return

        self.stdout.write(f"📊 Processing {queryset.count()} geocoding results...")

        # Run the validation using the function from validation.py
        stats = run_smart_validation(limit=options['limit'])

        # Show results for each location if requested
        if options['show_details']:
            self._show_detailed_results(queryset[:5])  # Show first 5 for details

        # Final summary
        self._show_summary(stats)

    def _show_detailed_results(self, queryset):
        """Show detailed results for individual locations."""
        self.stdout.write("\n" + "="*50)
        self.stdout.write("📋 DETAILED RESULTS")
        self.stdout.write("="*50)
        
        for result in queryset:
            if hasattr(result, 'validation'):
                validation = result.validation
                self.stdout.write(f"\n🔍 {result.location_name}")
                
                # Show sources
                sources = []
                if result.hdx_success:
                    sources.append("HDX")
                if result.arcgis_success:
                    sources.append("ArcGIS")
                if result.google_success:
                    sources.append("Google")
                if result.nominatim_success:
                    sources.append("Nominatim")
                
                self.stdout.write(f"   📡 Sources: {', '.join(sources)}")
                self.stdout.write(f"   🎯 Confidence: {validation.confidence_score:.1%}")
                self.stdout.write(f"   📊 Status: {validation.validation_status}")
                
                if validation.validation_status == 'validated':
                    self.stdout.write(self.style.SUCCESS("   ✅ AUTO-VALIDATED"))
                elif validation.validation_status == 'needs_review':
                    self.stdout.write(self.style.WARNING("   ⚠️ NEEDS REVIEW"))
                else:
                    self.stdout.write(self.style.ERROR("   🚨 MANUAL REVIEW"))

    def _show_summary(self, stats):
        """Show validation summary."""
        self.stdout.write("\n" + "="*60)
        self.stdout.write(self.style.SUCCESS("🎯 VALIDATION SUMMARY"))
        self.stdout.write("="*60)
        
        total = stats['processed']
        if total > 0:
            auto_pct = (stats['auto_validated'] / total) * 100
            review_pct = (stats['needs_review'] / total) * 100
            manual_pct = (stats['pending'] / total) * 100
            
            self.stdout.write(f"📊 Processed: {total} locations")
            self.stdout.write(f"✅ Auto-validated: {stats['auto_validated']} ({auto_pct:.1f}%)")
            self.stdout.write(f"⚠️ Needs review: {stats['needs_review']} ({review_pct:.1f}%)")
            self.stdout.write(f"🚨 Manual review: {stats['pending']} ({manual_pct:.1f}%)")
            self.stdout.write(f"❌ Rejected: {stats['rejected']}")
            
            self.stdout.write(f"\n💡 Next steps:")
            if stats['auto_validated'] > 0:
                self.stdout.write(f"   ✅ {stats['auto_validated']} locations automatically validated")
            if stats['needs_review'] > 0:
                self.stdout.write(f"   ⚠️ {stats['needs_review']} locations need quick human review")
            if stats['pending'] > 0:
                self.stdout.write(f"   🚨 {stats['pending']} locations need detailed manual review")
        else:
            self.stdout.write("No results to process.")