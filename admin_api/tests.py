"""Tests for the admin bank-account verification endpoint (B3)."""

from django.test import TestCase
from rest_framework.test import APIClient

from authentication.models import AuthStore
from store_profile.models import StoreProfile, StoreBankAccount

VALID_SA_IBAN = "SA0380000000608010167519"


class AdminStoreBankAccountTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.store = AuthStore.objects.create(name="Alpha Motors")
        self.base = f"/api/admin/stores/{self.store.id}/bank-account"

    def _create_account(self):
        StoreProfile.objects.create(
            store=self.store,
            commercial_name_en="Alpha Motors LLC",
            owner_full_name="عبدالله الفهد",
        )
        return StoreBankAccount.objects.create(
            store=self.store,
            bank_name="Al Rajhi Bank",
            account_holder_name="Alpha Motors LLC",
            iban=VALID_SA_IBAN,
        )

    def test_get_returns_payload_with_verification_state(self):
        account = self._create_account()
        res = self.client.get(self.base)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["bank"]["bankName"], "Al Rajhi Bank")
        self.assertEqual(res.data["bank"]["iban"], VALID_SA_IBAN)
        self.assertFalse(res.data["isVerified"])

    def test_get_404_when_store_has_no_account(self):
        res = self.client.get(self.base)
        self.assertEqual(res.status_code, 404)

    def test_get_404_for_unknown_store(self):
        import uuid

        res = self.client.get(f"/api/admin/stores/{uuid.uuid4()}/bank-account")
        self.assertEqual(res.status_code, 404)

    def test_verify_and_revoke_roundtrip(self):
        self._create_account()
        res = self.client.put(self.base + "/verify", {"isVerified": True}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.data["isVerified"])
        account = StoreBankAccount.objects.get(store=self.store)
        self.assertTrue(account.is_verified)

        res = self.client.put(self.base + "/verify", {"isVerified": False}, format="json")
        self.assertEqual(res.status_code, 200)
        account = StoreBankAccount.objects.get(store=self.store)
        self.assertFalse(account.is_verified)

    def test_verify_404_without_account(self):
        res = self.client.put(self.base + "/verify", {"isVerified": True}, format="json")
        self.assertEqual(res.status_code, 404)

    def test_verify_requires_boolean_field(self):
        self._create_account()
        res = self.client.put(self.base + "/verify", {}, format="json")
        self.assertEqual(res.status_code, 400)
        res = self.client.put(
            self.base + "/verify", {"isVerified": "yes"}, format="json"
        )
        self.assertEqual(res.status_code, 400)
