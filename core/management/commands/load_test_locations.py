"""
Management command to load 100 diverse test locations for geocoding efficiency testing.
Focuses on marginalized regions globally with varying name structures.
"""
from django.core.management.base import BaseCommand
from core.models import Location


class Command(BaseCommand):
    help = 'Load 100 diverse test locations from marginalized regions for geocoding testing'

    def handle(self, *args, **options):
        # 100 diverse location names from marginalized regions
        # Mix of: facility only, facility + city, facility + city + country, variations
        test_locations = [
            # AFRICA - Zimbabwe (15 locations)
            "Parirenyatwa Hospital Harare Zimbabwe",
            "Chitungwiza General Hospital",
            "Mpilo Hospital Bulawayo",
            "Mutare Provincial Hospital Zimbabwe",
            "St Mary's Mission Hospital Wedza",
            "Karanda Mission Hospital",
            "Makumbi Clinic Chiredzi",
            "Gweru District Hospital",
            "All Souls Mission Hospital Mutoko Zimbabwe",
            "Bonda Mission Hospital Mutare",
            "Chikwawa Health Centre",
            "United Bulawayo Hospitals",
            "Parirenyatwa Group of Hospitals",
            "Sally Mugabe Central Hospital Harare",
            "Bindura Provincial Hospital",

            # AFRICA - Malawi (10 locations)
            "Kamuzu Central Hospital Lilongwe Malawi",
            "Queen Elizabeth Central Hospital Blantyre",
            "Mzuzu Central Hospital",
            "St Luke's Hospital Zomba Malawi",
            "Mangochi District Hospital",
            "Nkhata Bay District Hospital Malawi",
            "Karonga District Hospital",
            "Mulanje Mission Hospital",
            "Chiradzulu District Hospital Malawi",
            "Thyolo District Hospital",

            # AFRICA - Zambia (8 locations)
            "University Teaching Hospital Lusaka Zambia",
            "Levy Mwanawasa Hospital Lusaka",
            "Ndola Central Hospital",
            "Kitwe Central Hospital Zambia",
            "Livingstone General Hospital",
            "St Francis Hospital Katete Zambia",
            "Monze Mission Hospital",
            "Mansa General Hospital Zambia",

            # AFRICA - Tanzania (8 locations)
            "Muhimbili National Hospital Dar es Salaam Tanzania",
            "Kilimanjaro Christian Medical Centre Moshi",
            "Bugando Medical Centre Mwanza Tanzania",
            "Mbeya Zonal Referral Hospital",
            "Dodoma Regional Hospital Tanzania",
            "Arusha Lutheran Medical Centre",
            "Tumbi Hospital Pwani Tanzania",
            "Sengerema District Hospital",

            # AFRICA - Kenya (8 locations)
            "Kenyatta National Hospital Nairobi Kenya",
            "Moi Teaching Hospital Eldoret",
            "Coast General Hospital Mombasa Kenya",
            "Kisumu County Hospital",
            "Nakuru Provincial Hospital Kenya",
            "Garissa Provincial Hospital",
            "Kakamega County Hospital Kenya",
            "Nyeri County Hospital",

            # AFRICA - Uganda (7 locations)
            "Mulago National Hospital Kampala Uganda",
            "Lacor Hospital Gulu",
            "Mbarara Regional Referral Hospital Uganda",
            "Mbale Regional Hospital",
            "Arua Regional Hospital Uganda",
            "Jinja Regional Hospital",
            "Fort Portal Regional Hospital Uganda",

            # AFRICA - Ethiopia (6 locations)
            "Black Lion Hospital Addis Ababa Ethiopia",
            "Tikur Anbessa Hospital Addis Ababa",
            "Jimma University Medical Center",
            "Gondar University Hospital Ethiopia",
            "Mekelle Hospital",
            "Hawassa University Hospital Ethiopia",

            # AFRICA - Democratic Republic of Congo (5 locations)
            "Cliniques Universitaires de Kinshasa",
            "Hopital General de Kinshasa DRC",
            "Hopital Mama Yemo Kinshasa",
            "Hopital Provincial de Goma",
            "Centre Hospitalier Lubumbashi Congo",

            # AFRICA - Somalia (4 locations)
            "Benadir Hospital Mogadishu Somalia",
            "Hargeisa Group Hospital",
            "Bay Regional Hospital Baidoa Somalia",
            "Kismayo General Hospital",

            # AFRICA - South Sudan (3 locations)
            "Juba Teaching Hospital South Sudan",
            "Malakal Teaching Hospital",
            "Wau Teaching Hospital South Sudan",

            # AFRICA - Chad (3 locations)
            "Hôpital Général de N'Djamena Chad",
            "Hôpital de l'Union Ndjamena",
            "Hôpital Regional Moundou Chad",

            # ASIA - Bangladesh (5 locations)
            "Dhaka Medical College Hospital Bangladesh",
            "Chittagong Medical College Hospital",
            "Cox's Bazar District Hospital Bangladesh",
            "Sylhet MAG Osmani Medical College",
            "Rangpur Medical College Hospital Bangladesh",

            # ASIA - Myanmar (4 locations)
            "Yangon General Hospital Myanmar",
            "Mandalay General Hospital",
            "Naypyidaw Hospital Myanmar",
            "Taunggyi General Hospital",

            # ASIA - Nepal (4 locations)
            "Tribhuvan University Teaching Hospital Kathmandu Nepal",
            "Bir Hospital Kathmandu",
            "Patan Hospital Nepal",
            "BP Koirala Institute Dharan Nepal",

            # ASIA - Afghanistan (3 locations)
            "Jamhuriat Hospital Kabul Afghanistan",
            "Indira Gandhi Children Hospital Kabul",
            "Herat Regional Hospital Afghanistan",

            # LATIN AMERICA - Haiti (3 locations)
            "Hopital Universitaire de Mirebalais Haiti",
            "Hopital General Port-au-Prince",
            "Hospital Immaculée Conception Les Cayes Haiti",

            # LATIN AMERICA - Bolivia (3 locations)
            "Hospital de Clínicas La Paz Bolivia",
            "Hospital Viedma Cochabamba",
            "Hospital San Juan de Dios Tarija Bolivia",

            # PACIFIC - Papua New Guinea (2 locations)
            "Port Moresby General Hospital PNG",
            "Goroka Base Hospital Papua New Guinea",

            # PACIFIC - Solomon Islands (2 locations)
            "National Referral Hospital Honiara",
            "Gizo Hospital Solomon Islands",
        ]

        self.stdout.write(self.style.SUCCESS(f'Loading {len(test_locations)} test locations...'))

        created_count = 0
        skipped_count = 0

        for location_name in test_locations:
            # Check if location already exists
            if Location.objects.filter(name__iexact=location_name).exists():
                self.stdout.write(self.style.WARNING(f'  Skipped (exists): {location_name}'))
                skipped_count += 1
            else:
                Location.objects.create(name=location_name)
                self.stdout.write(self.style.SUCCESS(f'  ✓ Created: {location_name}'))
                created_count += 1

        self.stdout.write(self.style.SUCCESS(f'\n✅ Complete!'))
        self.stdout.write(self.style.SUCCESS(f'   Created: {created_count} new locations'))
        self.stdout.write(self.style.SUCCESS(f'   Skipped: {skipped_count} existing locations'))
        self.stdout.write(self.style.SUCCESS(f'   Total: {len(test_locations)} locations'))
