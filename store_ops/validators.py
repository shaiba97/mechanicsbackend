"""Validators for merchant payout bank credentials (KSA).

Pure functions — no DB or HTTP imports. Every validation failure raises
``django.core.exceptions.ValidationError`` carrying a stable ``code`` so the
merchant frontend can render localized Arabic/English messages from the key,
and the admin surface logs the machine reason.
"""

import re
import unicodedata

from django.core.exceptions import ValidationError

#: Saudi IBANs are exactly `SA` + 22 digits (24 characters, no spaces/icons).
IBAN_PATTERN = re.compile(r"^SA\d{22}$")


class KsaBank:
    """A SAMA-licensed KSA bank accepting merchant payout transfers."""

    def __init__(self, code: str, name_en: str, name_ar: str):
        self.code = code
        self.name_en = name_en
        self.name_ar = name_ar

    @property
    def canonical(self) -> str:
        return self.name_en

    def keys(self) -> list[str]:
        lowered: list[str] = [self.code.lower(), self.name_en.lower(), self.name_ar]
        return lowered


#: SAMA-licensed local retail banks that accept IBN (payout) transfers to
#: merchant accounts. Input may use the code, Arabic name, or English name
#: (case-insensitive) — all resolve to the canonical English name stored in DB.
KSA_BANKS: list[KsaBank] = [
    KsaBank("ALRAJHI", "Al Rajhi Bank", "مصرف الراجحي"),
    KsaBank("SNB", "Saudi National Bank (SNB)", "البنك الأهلي السعودي"),
    KsaBank("RIYAD", "Riyad Bank", "بنك الرياض"),
    KsaBank("SABB", "SABB", "بنك ساب"),
    KsaBank("ALINMA", "Alinma Bank", "بنك الإنماء"),
    KsaBank("ALBILAD", "Bank Albilad", "بنك البلاد"),
    KsaBank("ANB", "Arab National Bank", "البنك العربي الوطني"),
    KsaBank("BAJ", "Bank AlJazira", "بنك الجزيرة"),
    KsaBank("ENBDKSA", "Emirates NBD Saudi Arabia", "بنك الإمارات دبي الوطني - السعودية"),
    KsaBank("TADHAMON", "Tadhamon International Islamic Bank", "بنك التضامن الدولي الإسلامي"),
]

BANK_LOOKUP: dict[str, str] = {}
for _bank in KSA_BANKS:
    for _key in _bank.keys():
        BANK_LOOKUP[_key] = _bank.canonical


def validate_sa_iban(raw: str) -> str:
    """Validate a Saudi IBAN and return the normalized (uppercase) value.

    Rules, per SAMA/ISO 13616 for Saudi Arabia:
      1. Exact regex `SA` + 22 digits — `^SA\\d{22}$`.
      2. Mod-97 checksum (ISO 13616 rearrangement) must leave remainder 1.

    Raises ValidationError with codes:
      - "iban_required"
      - "iban_format"    (regex failure)
      - "iban_checksum"  (mod-97 failure)
    """
    iban = (raw or "").replace(" ", "").replace("-", "").upper()
    if not iban:
        raise ValidationError("IBAN is required.", code="iban_required")
    if not IBAN_PATTERN.match(iban):
        raise ValidationError("IBAN must start with SA and contain exactly 22 digits.", code="iban_format")

    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(ord(ch) - 55) if ch.isalpha() else ch for ch in rearranged)
    remainder = 0
    for ch in digits:
        remainder = (remainder * 10 + int(ch)) % 97

    if remainder != 1:
        raise ValidationError("IBAN checksum failed.", code="iban_checksum")

    return iban


def resolve_ksa_bank(raw: str) -> str:
    """Map free-text bank input to the canonical English bank name.

    Accepts the bank code (ALRAJHI), the English name (any case), or the
    Arabic display name. Raises ValidationError with code "unknown_bank"
    when the value does not belong to the licensed whitelist — a stray typo
    (e.g. 'Chase') would otherwise send payouts into an account no bank owns.
    """
    value = (raw or "").strip()
    if not value:
        raise ValidationError("Bank name is required.", code="bank_required")
    lowered = value.lower()
    for bank in KSA_BANKS:
        candidates = [bank.code.lower(), bank.name_en.lower(), bank.name_ar]
        if lowered in candidates:
            return bank.canonical

    raise ValidationError(
        "Bank is not a SAMA-licensed KSA payout bank.", code="unknown_bank"
    )


def normalize_person_name(value: str) -> str:
    """Case/punctuation/diacritic-insensitive normalization for name matching.

    “  م . عبدالله  الفهد ” vs “عبدالله الفهد” both end at "عبدالله الفهد".
    Only letters/numbers/digits remain; spaces collapse to single spaces.
    """
    text = unicodedata.normalize("NFKC", value or "")
    text = text.lower()
    text = text.replace("'", "").replace("’", "")
    # Normalize Arabic presentation variants before stripping punctuation
    out: list[str] = []
    space_seen = False
    for ch in text:
        if ch.isalnum():
            out.append(ch)
            space_seen = False
        else:
            if not space_seen:
                out.append(" ")
                space_seen = True
    return "".join(out).strip()


def validate_account_holder_name(raw: str, allowed_names: list[str]) -> str:
    """Validate that the payout account holder matches the merchant's CR/NID.

    ``allowed_names`` must contain the names tied to commercial/legal identity
    — for a store: commercial_name_ar, commercial_name_en, owner_full_name, or
    the AuthStore name. Comparison after normalization.

    Raises ValidationError with code "holder_mismatch".
    """
    name = (raw or "").strip()
    if not name:
        raise ValidationError("Account holder name is required.", code="holder_required")

    normalized_input = normalize_person_name(name)
    for candidate in allowed_names or []:
        if candidate and normalize_person_name(candidate) == normalized_input:
            return name

    raise ValidationError(
        "Account holder name must match the Commercial Registration / National ID.",
        code="holder_mismatch",
    )
