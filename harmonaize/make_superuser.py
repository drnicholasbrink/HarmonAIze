#!/usr/bin/env python
"""
Quick script to make a user a superuser.
Run this with: python manage.py shell < make_superuser.py
"""
from harmonaize.users.models import User

email = "craig.parker@wtisphr.org"

try:
    user = User.objects.get(email=email)
    user.is_staff = True
    user.is_superuser = True
    user.save()
    print(f"✅ {email} is now a superuser!")
except User.DoesNotExist:
    print(f"❌ User {email} does not exist. Please sign up first.")
