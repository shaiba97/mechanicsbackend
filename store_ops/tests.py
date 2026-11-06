"""Tests for store payout account configuration (B1/B2) and ledger (B4/B5).

Run with:  python manage.py test store_ops --settings=backend.settings_test
"""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from authentication.models import AuthStore, User
from customer.models import Order
from store_ops import validators
from store_ops.models import StorePayoutRequest, TransactionLedger
from store_profile.models import StoreBankAccount, StoreProfile

VALID_SA_IBAN = "SA0380000000608010167519"  # documented SAMA/ISO example
VALID_GB_IBAN = "GB82WEST12345698765432"  # ISO 13616 cross-check vector


def violation_codes(exc: ValidationError) -> set[str]:
    try:
        return {e.code for e in exc.error_list}
    except AttributeError:
        return {exc.code}


class SaIbanValidatorTest(TestCase):
    def test_accepts_documented_sama_example(self):
        self.assertEqual(validators.validate_sa_iban(VALID_SA_IBAN), VALID_SA_IBAN)

    def test_accepts_spaced_and_lowercase_input(self):
        raw = "sa03 8000-0000 6080 1016 7519"
        self.assertEqual(validators.validate_sa_iban(raw), VALID_SA_IBAN)

    def test_rejects_short_long_and_non_digit(self):
        for raw in (
            "SA028000000060801016751",  # 21 digits
            "SA03800000006080101675199",  # 23 digits
            "GB82WEST12345698765432",  # not SA
            "SA03800X000060801016751Y",  # letters inside
            "SA0380000000608010167519 ",  # space counts after strip? stripped -> valid
            "",
        ):
            if raw == "SA0380000000608010167519 ":
                continue
            with self.assertRaises(ValidationError) as ctx:
                validators.validate_sa_iban(raw)
            self.assertTrue(
                violation_codes(ctx.exception) & {"iban_required", "iban_format"},
                f"expected format error for {raw!r}, got {violation_codes(ctx.exception)}",
            )

    def test_rejects_bad_checksum(self):
        with self.assertRaises(ValidationError) as ctx:
            validators.validate_sa_iban("SA0280000000608010167519")  # 22 digits, wrong CDN
        self.assertIn("iban_checksum", violation_codes(ctx.exception))


class KsaBankResolverTest(TestCase):
    def test_accepts_code_english_and_arabic(self):
        for raw in ("alrajhi", "ALRAJHI", "Al Rajhi Bank", "مصرف الراجحي"):
            self.assertEqual(validators.resolve_ksa_bank(raw), "Al Rajhi Bank")

    def test_accepts_second_bank(self):
        self.assertEqual(
            validators.resolve_ksa_bank("البنك الأهلي السعودي"),
            "Saudi National Bank (SNB)",
        )

    def test_rejects_unlicensed_bank(self):
        with self.assertRaises(ValidationError) as ctx:
            validators.resolve_ksa_bank("Chase")
        self.assertIn("unknown_bank", violation_codes(ctx.exception))

    def test_blank_is_required_error(self):
        with self.assertRaises(ValidationError) as ctx:
            validators.resolve_ksa_bank("  ")
        self.assertIn("bank_required", violation_codes(ctx.exception))


class HolderNameMatchTest(TestCase):
    ALLOWED = [
        "Alpha Motors LLC",
        "Alpha Motors Est للتجارة",
        "عبدالله الفهد",
    ]

    def test_exact_en_match(self):
        self.assertEqual(
            validators.validate_account_holder_name("alpha motors llc", self.ALLOWED),
            "alpha motors llc",
        )

    def test_punctuation_and_extra_spaces_normalized(self):
        self.assertEqual(
            validators.validate_account_holder_name('Abdullah  Al-Fahad, "LLC"', ["Abdullah Al-Fahad LLC"]),
            'Abdullah  Al-Fahad, "LLC"',
        )

    def test_arabic_exact_match(self):
        self.assertEqual(
            validators.validate_account_holder_name("عبدالله الفهد", self.ALLOWED),
            "عبدالله الفهد",
        )

    def test_mismatch_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            validators.validate_account_holder_name("Random Trading Co", self.ALLOWED)
        self.assertIn("holder_mismatch", violation_codes(ctx.exception))

    def test_blank_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            validators.validate_account_holder_name("  ", self.ALLOWED)
        self.assertIn("holder_required", violation_codes(ctx.exception))


class StoreBankAccountApiTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.store = AuthStore.objects.create(name="Alpha Motors")
        self.user = User.objects.create_user(
            email="mgr@alpha.example",
            password="pw123456",
            full_name="Store Manager",
            role="STORE_ADMIN",
            store=self.store,
        )
        StoreProfile.objects.create(
            store=self.store,
            commercial_name_en="Alpha Motors LLC",
            owner_full_name="عبدالله الفهد",
        )
        self.client.force_authenticate(self.user)
        self.url = "/api/store/bank-account"

    def _post(self, **overrides):
        payload = {
            "bankName": "alrajhi",
            "accountHolderName": "Alpha Motors LLC",
            "iban": "SA03 8000 0000 6080 1016 7519",
        }
        payload.update(overrides)
        return self.client.post(self.url, payload, format="json")

    def test_happy_path_saves_canonical_bank_and_normalized_iban(self):
        res = self._post()
        self.assertEqual(res.status_code, 201, res.content)
        self.assertFalse(res.data["isVerified"])
        account = StoreBankAccount.objects.get(store=self.store)
        self.assertEqual(account.bank_name, "Al Rajhi Bank")
        self.assertEqual(account.iban, VALID_SA_IBAN)
        self.assertEqual(account.account_holder_name, "Alpha Motors LLC")

    def test_second_post_updates_not_duplicates(self):
        self._post()
        res = self._post(bankName="Alinma Bank")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(StoreBankAccount.objects.filter(store=self.store).count(), 1)
        self.assertEqual(StoreBankAccount.objects.get(store=self.store).bank_name, "Alinma Bank")

    def test_holder_mismatch_is_400_with_code(self):
        res = self._post(accountHolderName="Random Trading")
        self.assertEqual(res.status_code, 400)
        codes = {e["code"] for e in res.data.get("errors", [])}
        self.assertIn("holder_mismatch", codes)
        # Nothing persisted.
        self.assertFalse(StoreBankAccount.objects.filter(store=self.store).exists())

    def test_unknown_bank_is_400_with_code(self):
        res = self._post(bankName="Chase")
        self.assertEqual(res.status_code, 400)
        codes = {e["code"] for e in res.data.get("errors", [])}
        self.assertIn("unknown_bank", codes)

    def test_bad_checksum_is_400_with_code(self):
        res = self._post(iban="SA0280000000608010167519")
        self.assertEqual(res.status_code, 400)
        codes = {e["code"] for e in res.data.get("errors", [])}
        self.assertIn("iban_checksum", codes)

    def test_bad_format_is_400_with_code(self):
        res = self._post(iban="SA03800X000060801016751Y")
        self.assertEqual(res.status_code, 400)
        codes = {e["code"] for e in res.data.get("errors", [])}
        self.assertIn("iban_format", codes)

    def test_blank_fields_yield_structured_required_errors(self):
        res = self.client.post(self.url, {}, format="json")
        self.assertEqual(res.status_code, 400)
        codes = {e["code"] for e in res.data.get("errors", [])}
        self.assertEqual(
            codes,
            {"bank_required", "holder_required", "iban_required"},
        )


class StorePayoutsApiTest(TestCase):
    """B4: effective status, commissionPercent, ?status filter, summary."""

    def setUp(self):
        self.client = APIClient()
        self.store = AuthStore.objects.create(name="Alpha Motors")
        self.user = User.objects.create_user(
            email="mgr@alpha.example",
            password="pw123456",
            full_name="Manager",
            role="STORE_ADMIN",
            store=self.store,
        )
        self.customer = User.objects.create_user(
            email="c@example.com",
            password="pw123456",
            full_name="Customer",
            role="CUSTOMER",
        )
        self.client.force_authenticate(self.user)
        self.url = "/api/store/payouts"

    def _ledger(self, ref, status, due_delta, total="100.00", commission="15.00", payout="85.00"):
        # Order is created in its default PENDING state so the completion
        # signal does not fire; the ledger row is built explicitly below.
        # commission_percent stores a fraction (0.1500 == 15%).
        order = Order.objects.create(
            user=self.customer,
            store=self.store,
            reference_code=ref,
            total_sar=Decimal(total),
            grand_total_sar=Decimal(total),
        )
        return TransactionLedger.objects.create(
            order=order,
            store=self.store,
            total_amount=Decimal(total),
            commission_percent=Decimal("0.1500"),
            commission_amount=Decimal(commission),
            store_payout_amount=Decimal(payout),
            payout_status=status,
            payout_due_at=timezone.now() + due_delta,
        )

    def setUpLedger(self):
        self.held = self._ledger("REF1", TransactionLedger.PayoutStatus.HELD, timedelta(hours=1))
        self.ready = self._ledger("REF2", TransactionLedger.PayoutStatus.HELD, timedelta(hours=-1))
        self.processing = self._ledger(
            "REF3", TransactionLedger.PayoutStatus.PROCESSING, timedelta(hours=-1), total="200.00", commission="30.00", payout="170.00"
        )
        self.paid = self._ledger(
            "REF4", TransactionLedger.PayoutStatus.PAID, timedelta(hours=-2), total="50.00", commission="7.50", payout="42.50"
        )
        self.failed = self._ledger("REF5", TransactionLedger.PayoutStatus.FAILED, timedelta(hours=-1))
        return [self.held, self.ready, self.processing, self.paid, self.failed]

    def test_effective_status_and_commission_percent(self):
        self.setUpLedger()
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        by_status = {}
        for row in res.data["results"]:
            by_status.setdefault(row["status"], []).append(row)
        self.assertEqual(len(by_status["held"]), 1)
        self.assertEqual(len(by_status["ready_for_payout"]), 1)
        self.assertEqual(len(by_status["processing"]), 1)
        self.assertEqual(len(by_status["paid"]), 1)
        self.assertEqual(len(by_status["failed"]), 1)
        ready = by_status["ready_for_payout"][0]
        self.assertAlmostEqual(ready["commissionPercent"], 15.0, places=6)
        self.assertEqual(ready["payoutStatus"], "held")

    def test_status_filter(self):
        self.setUpLedger()
        res = self.client.get(self.url + "?status=ready_for_payout")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data["results"]), 1)
        self.assertEqual(res.data["results"][0]["status"], "ready_for_payout")

        res = self.client.get(self.url + "?status=held")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            [r["status"] for r in res.data["results"]],
            ["held"],
        )

    def test_invalid_status_filter_400(self):
        self.setUpLedger()
        res = self.client.get(self.url + "?status=bogus")
        self.assertEqual(res.status_code, 400)
        self.assertIn("validStatuses", res.data)

    def test_summary_aggregates(self):
        self.setUpLedger()
        res = self.client.get(self.url)
        ledger = res.data["summary"]["ledger"]
        self.assertEqual(ledger["held"], {"count": 1, "amountSar": 85.0})
        self.assertEqual(ledger["readyForPayout"], {"count": 1, "amountSar": 85.0})
        self.assertEqual(ledger["processing"], {"count": 1, "amountSar": 170.0})
        self.assertEqual(ledger["paid"], {"count": 1, "amountSar": 42.5})
        self.assertEqual(ledger["failed"], {"count": 1, "amountSar": 85.0})

    def test_requires_store_link(self):
        customer_only = User.objects.filter(role="CUSTOMER").first()
        self.client.force_authenticate(customer_only)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 403)


class LedgerSignalTest(TestCase):
    """The completion signal creates a HELD ledger snapshot (7-day escrow)."""

    def setUp(self):
        self.store = AuthStore.objects.create(name="Alpha Motors")
        self.customer = User.objects.create_user(
            email="c@example.com",
            password="pw123456",
            full_name="Customer",
            role="CUSTOMER",
        )

    def _completed_order(self):
        order = Order.objects.create(
            user=self.customer,
            store=self.store,
            total_sar=Decimal("100.00"),
            grand_total_sar=Decimal("100.00"),
            reference_code="SIGNAL-1",
        )
        order.status = Order.Status.COMPLETED
        order.payment_status = Order.PaymentStatus.PAID
        order.save()
        return order

    def test_completed_paid_order_creates_single_held_ledger(self):
        order = self._completed_order()
        ledgers = TransactionLedger.objects.filter(order=order)
        self.assertEqual(ledgers.count(), 1)
        ledger = ledgers.first()
        self.assertEqual(ledger.payout_status, TransactionLedger.PayoutStatus.HELD)
        self.assertEqual(ledger.commission_percent, Decimal("0.1000"))
        self.assertEqual(ledger.commission_amount, Decimal("10.00"))
        self.assertEqual(ledger.store_payout_amount, Decimal("90.00"))
        self.assertAlmostEqual(
            ledger.payout_due_at.timestamp(),
            (order.updated_at + timedelta(days=7)).timestamp(),
            delta=5,
        )

    def test_resaving_order_does_not_reflow_ledger(self):
        order = self._completed_order()
        before = list(TransactionLedger.objects.values_list("commission_amount", flat=True))
        order.save()  # still COMPLETED + PAID
        after = list(TransactionLedger.objects.values_list("commission_amount", flat=True))
        self.assertEqual(len(after), 1)
        self.assertEqual(before, after)

    def test_unpaid_or_pending_orders_create_no_ledger(self):
        Order.objects.create(
            user=self.customer, store=self.store, reference_code="NOLEDGER-1"
        )
        self.assertEqual(TransactionLedger.objects.count(), 0)


class FinancialsSummaryApiTest(TestCase):
    """B5: grossSalesSar + commissionSar surfaced on financials summary."""

    def setUp(self):
        self.client = APIClient()
        self.store = AuthStore.objects.create(name="Alpha Motors")
        self.user = User.objects.create_user(
            email="mgr@alpha.example",
            password="pw123456",
            full_name="Manager",
            role="STORE_ADMIN",
            store=self.store,
        )
        self.customer = User.objects.create_user(
            email="c@example.com",
            password="pw123456",
            full_name="Customer",
            role="CUSTOMER",
        )
        self.client.force_authenticate(self.user)
        self.url = "/api/store/financials/summary"

    def test_gross_and_commission_for_completed_paid_order(self):
        order = Order.objects.create(
            user=self.customer,
            store=self.store,
            total_sar=Decimal("100.00"),
            grand_total_sar=Decimal("100.00"),
            reference_code="SUM-1",
        )
        order.status = Order.Status.COMPLETED
        order.payment_status = Order.PaymentStatus.PAID
        order.save()

        res = self.client.get(self.url + "?period=month")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertAlmostEqual(res.data["grossSalesSar"], 100.0, places=2)
        self.assertAlmostEqual(res.data["commissionSar"], 10.0, places=2)
        self.assertAlmostEqual(res.data["escrowHeldSar"], 90.0, places=2)
        self.assertAlmostEqual(res.data["netPayoutSar"], 90.0, places=2)
        self.assertEqual(res.data["orderCount"], 1)
        self.assertEqual(res.data["completedCount"], 1)

    def test_invalid_period_falls_back_to_month(self):
        res = self.client.get(self.url + "?period=bogus")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["period"], "month")


class ExecutePayoutsApiTest(TestCase):
    """B6: gateway execution + StorePayoutRequest audit batch."""

    def setUp(self):
        self.client = APIClient()
        self.store = AuthStore.objects.create(name="Alpha Motors")
        self.user = User.objects.create_user(
            email="mgr@alpha.example",
            password="pw123456",
            full_name="Manager",
            role="STORE_ADMIN",
            store=self.store,
        )
        self.customer = User.objects.create_user(
            email="c@example.com",
            password="pw123456",
            full_name="Customer",
            role="CUSTOMER",
        )
        self.client.force_authenticate(self.user)
        self.url = "/api/store/payouts/execute"
        self.account = None

    def _prepare(self, verified=True, with_account=True):
        if with_account:
            StoreProfile.objects.create(
                store=self.store,
                commercial_name_en="Alpha Motors LLC",
                owner_full_name="??????? ?????",
            )
            self.account = StoreBankAccount.objects.create(
                store=self.store,
                bank_name="Al Rajhi Bank",
                account_holder_name="Alpha Motors LLC",
                iban=VALID_SA_IBAN,
                is_verified=verified,
            )
        return self._make_ledger("EXEC-1"), self._make_ledger("EXEC-2")

    def _make_ledger(self, ref, total="100.00", payout="85.00"):
        order = Order.objects.create(
            user=self.customer,
            store=self.store,
            reference_code=ref,
            total_sar=Decimal(total),
            grand_total_sar=Decimal(total),
        )
        return TransactionLedger.objects.create(
            order=order,
            store=self.store,
            total_amount=Decimal(total),
            commission_percent=Decimal("0.1500"),
            commission_amount=Decimal("15.00"),
            store_payout_amount=Decimal(payout),
            payout_status=TransactionLedger.PayoutStatus.HELD,
            payout_due_at=timezone.now() - timedelta(hours=1),
        )

    def test_missing_bank_account_is_400_with_code(self):
        self._prepare(with_account=False)
        res = self.client.post(self.url)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "bank_account_missing")

    def test_unverified_bank_account_is_400_with_code(self):
        self._prepare(verified=False)
        res = self.client.post(self.url)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "bank_account_unverified")

    def test_no_due_payouts_is_400(self):
        self._prepare()
        # Ledger rows exist but their escrow window has not elapsed yet.
        TransactionLedger.objects.all().update(
            payout_due_at=timezone.now() + timedelta(days=1)
        )
        res = self.client.post(self.url)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["detail"], "No payouts due for execution.")

    def test_executes_paid_batch_with_audit(self):
        l1, l2 = self._prepare()
        res = self.client.post(self.url)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data["count"], 2)
        self.assertEqual(res.data["failedCount"], 0)
        self.assertTrue(all(e["gatewayTransactionId"].startswith("TRF-") for e in res.data["executed"]))

        l1.refresh_from_db()
        l2.refresh_from_db()
        self.assertEqual(l1.payout_status, TransactionLedger.PayoutStatus.PAID)
        self.assertTrue(l1.paid_at is not None)
        self.assertTrue(l1.gateway_transaction_id.startswith("TRF-"))

        audit = StorePayoutRequest.objects.filter(store=self.store).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.status, StorePayoutRequest.Status.PAID)
        self.assertAlmostEqual(float(audit.amount_sar), 170.0, places=2)
        self.assertEqual(str(payout_request_id := audit.id), res.data["payoutRequestId"])

    def test_gateway_failure_marks_ledgers_and_audit_failed(self):
        from unittest import mock

        l1, l2 = self._prepare()
        with mock.patch(
            "store_ops.views.execute_iban_transfer",
            return_value={"success": False, "transfer_id": None, "error": "gateway down"},
        ):
            res = self.client.post(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["count"], 0)
        self.assertEqual(res.data["failedCount"], 2)
        self.assertEqual(res.data["payoutRequestStatus"], StorePayoutRequest.Status.FAILED)

        l1.refresh_from_db()
        self.assertEqual(l1.payout_status, TransactionLedger.PayoutStatus.FAILED)
        self.assertEqual(l1.gateway_transaction_id, "")


class ProcessStorePayoutsTaskTest(TestCase):
    """B6: Celery task pays per-store batches and skips unverified stores."""

    def setUp(self):
        self.store_a = AuthStore.objects.create(name="Store A")
        self.store_b = AuthStore.objects.create(name="Store B")
        self.customer = User.objects.create_user(
            email="c@example.com",
            password="pw123456",
            full_name="Customer",
            role="CUSTOMER",
        )
        StoreBankAccount.objects.create(
            store=self.store_a,
            bank_name="Al Rajhi Bank",
            account_holder_name="Store A",
            iban=VALID_SA_IBAN,
            is_verified=True,
        )

    def _ledger(self, store, ref):
        order = Order.objects.create(
            user=self.customer,
            store=store,
            reference_code=ref,
            total_sar=Decimal("100.00"),
            grand_total_sar=Decimal("100.00"),
        )
        return TransactionLedger.objects.create(
            order=order,
            store=store,
            total_amount=Decimal("100.00"),
            commission_percent=Decimal("0.1500"),
            commission_amount=Decimal("15.00"),
            store_payout_amount=Decimal("85.00"),
            payout_status=TransactionLedger.PayoutStatus.HELD,
            payout_due_at=timezone.now() - timedelta(days=1),
        )

    def test_task_pays_verified_store_and_skips_unverified(self):
        from store_ops.tasks import process_store_payouts

        l1 = self._ledger(self.store_a, "TASK-1")
        l2 = self._ledger(self.store_a, "TASK-2")
        l3 = self._ledger(self.store_b, "TASK-3")

        result = process_store_payouts()
        self.assertEqual(result, {"processed": 2, "success": 2, "failed": 0, "skipped": 1})

        l1.refresh_from_db()
        l2.refresh_from_db()
        l3.refresh_from_db()
        self.assertEqual(l1.payout_status, TransactionLedger.PayoutStatus.PAID)
        self.assertEqual(l2.payout_status, TransactionLedger.PayoutStatus.PAID)
        self.assertEqual(l3.payout_status, TransactionLedger.PayoutStatus.HELD)
        self.assertTrue(l1.gateway_transaction_id.startswith("TRF-"))

        audits = StorePayoutRequest.objects.filter(store=self.store_a)
        self.assertEqual(audits.count(), 1)
        audit = audits.first()
        self.assertEqual(audit.status, StorePayoutRequest.Status.PAID)
        self.assertAlmostEqual(float(audit.amount_sar), 170.0, places=2)
        self.assertFalse(StorePayoutRequest.objects.filter(store=self.store_b).exists())

    def test_task_skips_libraries_when_nothing_due(self):
        from store_ops.tasks import process_store_payouts

        t = timezone.now() + timedelta(days=1)
        order = Order.objects.create(
            user=self.customer,
            store=self.store_a,
            reference_code="FUTURE-1",
            total_sar=Decimal("100.00"),
            grand_total_sar=Decimal("100.00"),
        )
        TransactionLedger.objects.create(
            order=order,
            store=self.store_a,
            total_amount=Decimal("100.00"),
            commission_percent=Decimal("0.1500"),
            commission_amount=Decimal("15.00"),
            store_payout_amount=Decimal("85.00"),
            payout_status=TransactionLedger.PayoutStatus.HELD,
            payout_due_at=t,
        )
        result = process_store_payouts()
        self.assertEqual(result, {"processed": 0, "success": 0, "failed": 0, "skipped": 0})


class OrderDeliveryTransitionsTest(TestCase):
    """Out-for-delivery transition graph + 4-digit OTP handover (delivery tracking)."""

    def setUp(self):
        self.client = APIClient()
        self.store = AuthStore.objects.create(name="Alpha Motors")
        self.user = User.objects.create_user(
            email="mgr@alpha.example",
            password="pw123456",
            full_name="Manager",
            role="STORE_ADMIN",
            store=self.store,
        )
        self.customer = User.objects.create_user(
            email="c@example.com",
            password="pw123456",
            full_name="Customer",
            role="CUSTOMER",
        )
        self.client.force_authenticate(self.user)

    def _order(self, ref, status=Order.Status.PENDING, **fields):
        data = dict(
            user=self.customer,
            store=self.store,
            reference_code=ref,
            status=status,
            total_sar=Decimal("100.00"),
            grand_total_sar=Decimal("100.00"),
            payment_method=Order.PaymentMethod.COD,
            payment_status=Order.PaymentStatus.UNPAID,
        )
        data.update(fields)
        return Order.objects.create(**data)

    def _status(self, order, target):
        return self.client.post(
            f"/api/store/orders/{order.id}/status", {"status": target}
        )

    def _verify(self, order, code):
        return self.client.post(
            f"/api/store/orders/{order.id}/verify-otp", {"otp": code}
        )

    def test_store_preparing_to_out_for_delivery_allowed(self):
        order = self._order("REF-D1", Order.Status.STORE_PREPARING)
        res = self._status(order, Order.Status.OUT_FOR_DELIVERY)
        self.assertEqual(res.status_code, 200, res.content)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.OUT_FOR_DELIVERY)

    def test_delivery_assigned_to_out_for_delivery_allowed(self):
        order = self._order("REF-D2", Order.Status.DELIVERY_ASSIGNED)
        res = self._status(order, Order.Status.OUT_FOR_DELIVERY)
        self.assertEqual(res.status_code, 200, res.content)

    def test_out_for_delivery_to_otp_generated_allowed(self):
        order = self._order("REF-D3", Order.Status.OUT_FOR_DELIVERY)
        res = self._status(order, Order.Status.OTP_GENERATED)
        self.assertEqual(res.status_code, 200, res.content)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.OTP_GENERATED)

    def test_store_preparing_to_otp_generated_still_allowed(self):
        order = self._order("REF-D4", Order.Status.STORE_PREPARING)
        res = self._status(order, Order.Status.OTP_GENERATED)
        self.assertEqual(res.status_code, 200, res.content)

    def test_out_for_delivery_to_completed_is_rejected(self):
        order = self._order("REF-D5", Order.Status.OUT_FOR_DELIVERY)
        res = self._status(order, Order.Status.COMPLETED)
        self.assertEqual(res.status_code, 400)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.OUT_FOR_DELIVERY)

    def test_otp_generated_only_on_otp_generated_transition(self):
        order = self._order("REF-D6", Order.Status.STORE_PREPARING)
        self._status(order, Order.Status.OUT_FOR_DELIVERY)
        order.refresh_from_db()
        self.assertEqual(order.handover_otp, "")

    def test_otp_is_exactly_4_digits(self):
        order = self._order("REF-D7", Order.Status.OUT_FOR_DELIVERY)
        self._status(order, Order.Status.OTP_GENERATED)
        order.refresh_from_db()
        self.assertRegex(order.handover_otp, r"^\d{4}$")

    def test_verify_otp_rejects_out_for_delivery(self):
        order = self._order("REF-D8", Order.Status.OUT_FOR_DELIVERY)
        res = self._verify(order, "0000")
        self.assertEqual(res.status_code, 409)

    def test_verify_otp_completes_order_and_marks_cod_paid(self):
        order = self._order(
            "REF-D9", Order.Status.OTP_GENERATED, handover_otp="1234"
        )
        res = self._verify(order, "1234")
        self.assertEqual(res.status_code, 200, res.content)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.assertEqual(order.payment_status, Order.PaymentStatus.PAID)

    def test_wrong_otp_is_400(self):
        order = self._order("REF-D10", Order.Status.OTP_GENERATED, handover_otp="0000")
        res = self._verify(order, "9999")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["detail"], "Wrong handover code.")

    def test_verify_otp_throttle_locks_after_five_failures(self):
        order = self._order("REF-D11", Order.Status.OTP_GENERATED, handover_otp="0000")
        for _ in range(4):
            res = self._verify(order, "9999")
            self.assertEqual(res.status_code, 400, res.content)
        res = self._verify(order, "9999")
        self.assertEqual(res.status_code, 429, res.content)

    def test_throttle_resets_on_success(self):
        order = self._order("REF-D12", Order.Status.OTP_GENERATED, handover_otp="0000")
        for _ in range(4):
            self._verify(order, "9999")
        res = self._verify(order, "0000")
        self.assertEqual(res.status_code, 200, res.content)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.assertFalse(StorePayoutRequest.objects.exists())
