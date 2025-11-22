from django.core.management.base import BaseCommand
from harmonaize.users.models import User


class Command(BaseCommand):
    help = "Make a user a superuser/admin"

    def add_arguments(self, parser):
        parser.add_argument("email", type=str, help="Email of the user to make admin")

    def handle(self, *args, **options):
        email = options["email"]

        try:
            user = User.objects.get(email=email)
            user.is_staff = True
            user.is_superuser = True
            user.save()
            self.stdout.write(
                self.style.SUCCESS(f"✅ {email} is now a superuser/admin!")
            )
        except User.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f"❌ User {email} does not exist. Please sign up first.")
            )
