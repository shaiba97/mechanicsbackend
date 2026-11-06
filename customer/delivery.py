from decimal import Decimal


class KsaDelivery:
    SLUGS = {
        "riyadh": {"ar": "الرياض", "en": "Riyadh", "fee": Decimal("20.00")},
        "kharj": {"ar": "الخرج", "en": "Al Kharj", "fee": Decimal("20.00")},
        "jeddah": {"ar": "جدة", "en": "Jeddah", "fee": Decimal("25.00")},
        "makkah": {"ar": "مكة المكرمة", "en": "Makkah", "fee": Decimal("25.00")},
        "dammam": {"ar": "الدمام", "en": "Dammam", "fee": Decimal("25.00")},
        "khobar": {"ar": "الخبر", "en": "Khobar", "fee": Decimal("25.00")},
        "dhahran": {"ar": "الظهران", "en": "Dhahran", "fee": Decimal("25.00")},
        "qatif": {"ar": "القطيف", "en": "Qatif", "fee": Decimal("25.00")},
        "madinah": {"ar": "المدينة المنورة", "en": "Madinah", "fee": Decimal("30.00")},
        "taif": {"ar": "الطائف", "en": "Taif", "fee": Decimal("35.00")},
        "buraidah": {"ar": "بريدة", "en": "Buraidah", "fee": Decimal("35.00")},
        "ahsa": {"ar": "الأحساء", "en": "Al Ahsa", "fee": Decimal("35.00")},
        "yanbu": {"ar": "ينبع", "en": "Yanbu", "fee": Decimal("40.00")},
        "abha": {"ar": "أبها", "en": "Abha", "fee": Decimal("40.00")},
        "tabuk": {"ar": "تبوك", "en": "Tabuk", "fee": Decimal("40.00")},
        "hail": {"ar": "حائل", "en": "Hail", "fee": Decimal("40.00")},
        "najran": {"ar": "نجران", "en": "Najran", "fee": Decimal("45.00")},
        "jazan": {"ar": "جازان", "en": "Jazan", "fee": Decimal("45.00")},
    }

    @classmethod
    def list(cls):
        return [
            {"slug": slug, "name": {"ar": info["ar"], "en": info["en"]}, "fee": float(info["fee"])}
            for slug, info in cls.SLUGS.items()
        ]

    @classmethod
    def is_valid(cls, slug: str) -> bool:
        return str(slug or "").strip().lower() in cls.SLUGS

    @classmethod
    def fee(cls, slug: str) -> Decimal | None:
        info = cls.SLUGS.get(str(slug or "").strip().lower())
        return info["fee"] if info else None
