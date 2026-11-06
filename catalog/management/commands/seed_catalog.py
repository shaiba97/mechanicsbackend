from django.core.management.base import BaseCommand

from catalog.models import Brand, Car, Category

BRANDS = [
    {
        "code": "TOYOTA",
        "name": {"ar": "تويوتا", "en": "Toyota"},
        "logo_url": "https://www.toyota.com/favicon.ico",
        "active": True,
    },
    {
        "code": "HYUNDAI",
        "name": {"ar": "هيونداي", "en": "Hyundai"},
        "logo_url": "https://www.hyundai.com/favicon.ico",
        "active": True,
    },
    {
        "code": "NISSAN",
        "name": {"ar": "نيسان", "en": "Nissan"},
        "logo_url": "https://www.nissan-global.com/favicon.ico",
        "active": True,
    },
    {
        "code": "FORD",
        "name": {"ar": "فورد", "en": "Ford"},
        "logo_url": "https://www.ford.com/favicon.ico",
        "active": True,
    },
    {
        "code": "KIA",
        "name": {"ar": "كيا", "en": "Kia"},
        "logo_url": "https://www.kia.com/favicon.ico",
        "active": False,
    },
]

CARS = [
    {"brand_code": "TOYOTA", "model": {"ar": "كامري", "en": "Camry"}, "year": 2024, "active": True},
    {"brand_code": "TOYOTA", "model": {"ar": "كورولا", "en": "Corolla"}, "year": 2023, "active": True},
    {"brand_code": "HYUNDAI", "model": {"ar": "سوناتا", "en": "Sonata"}, "year": 2024, "active": True},
    {"brand_code": "HYUNDAI", "model": {"ar": "إلنترا", "en": "Elantra"}, "year": 2022, "active": True},
    {"brand_code": "NISSAN", "model": {"ar": "التيما", "en": "Altima"}, "year": 2023, "active": True},
    {"brand_code": "FORD", "model": {"ar": "F-150", "en": "F-150"}, "year": 2024, "active": True},
    {"brand_code": "KIA", "model": {"ar": "سبورتاج", "en": "Sportage"}, "year": 2023, "active": False},
    {"brand_code": "KIA", "model": {"ar": "كاجا", "en": "Carnival"}, "year": 2022, "active": True},
]

CATEGORIES = [
    {"name": {"ar": "فرامل", "en": "Brakes"}, "active": True},
    {"name": {"ar": "محركات وفلترة", "en": "Engine & Filters"}, "active": True},
    {"name": {"ar": "مفاصل ونوابض", "en": "Suspension"}, "active": True},
    {"name": {"ar": "كهرباء وبطاريات", "en": "Electrics & Batteries"}, "active": True},
    {"name": {"ar": "هياكل وبودي", "en": "Body Panels"}, "active": True},
]


class Command(BaseCommand):
    help = "Seed catalog brands, cars and categories when the tables are empty."

    def handle(self, *args, **options):
        if Brand.objects.exists() or Category.objects.exists():
            self.stdout.write("Catalog already seeded; skipping.")
            return
        brands = {}
        for brand in BRANDS:
            instance = Brand.objects.create(**brand)
            brands[brand["code"]] = instance
        for car in CARS:
            Car.objects.create(
                brand=brands[car["brand_code"]],
                model_name=car["model"],
                year=car["year"],
                active=car["active"],
            )
        for category in CATEGORIES:
            Category.objects.create(**category)
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(BRANDS)} brands, {len(CARS)} cars, {len(CATEGORIES)} categories."
            )
        )
