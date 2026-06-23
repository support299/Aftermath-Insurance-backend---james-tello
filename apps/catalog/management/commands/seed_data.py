"""Seeds the same default rows the original Supabase migrations inserted."""

from django.core.management.base import BaseCommand

from apps.catalog.models import AddOn, Carrier, LeadSource, Product
from apps.company.models import CompanySettings
from apps.teams.models import Team

CARRIERS = [
    "UnitedHealth",
    "Anthem",
    "Cigna",
    "Aetna",
    "Humana",
    "Blue Cross Blue Shield",
    "Kaiser Permanente",
    "Molina Healthcare",
]

PRODUCTS = [
    "Medical",
    "Dental-only",
    "Vision-only",
    "Medical + Dental Bundle",
    "Medical + Dental + Vision Bundle",
    "Short-term Medical",
]

ADD_ONS = ["Dental", "Vision", "Accident", "Critical Illness", "Life"]

LEAD_SOURCES = ["Direct", "Referral", "Online", "Broker Network", "Cold Call", "Social Media"]

TEAMS = ["Alpha Squad", "Bravo Team", "Charlie Crew", "Delta Force"]


class Command(BaseCommand):
    help = "Seed default carriers, products, add-ons, lead sources, and teams"

    def handle(self, *args, **options):
        for name in CARRIERS:
            Carrier.objects.get_or_create(name=name)
        for name in PRODUCTS:
            Product.objects.get_or_create(name=name)
        for name in ADD_ONS:
            AddOn.objects.get_or_create(name=name)
        for name in LEAD_SOURCES:
            LeadSource.objects.get_or_create(name=name)
        for name in TEAMS:
            Team.objects.get_or_create(name=name)
        CompanySettings.load()
        self.stdout.write(self.style.SUCCESS("Seed data created."))
