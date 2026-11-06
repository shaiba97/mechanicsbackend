from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone

from customer.models import Order
from store_ops.models import TransactionLedger

from .models import PlatformCommissionRate


def paid_or_completed_q():
    return Q(status=Order.Status.COMPLETED) | Q(payment_status=Order.PaymentStatus.PAID)


def revenue_queryset():
    return (
        Order.objects.exclude(status=Order.Status.CANCELLED)
        .filter(paid_or_completed_q())
    )


def period_range(period, now):
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1) - timedelta(microseconds=1)
    if period == "day":
        return today_start, today_end
    if period == "week":
        return today_start - timedelta(days=6), today_end
    if period == "month":
        return today_start.replace(day=1), today_end
    if period == "quarter":
        month = today_start.month
        q_start_month = ((month - 1) // 3) * 3 + 1
        return today_start.replace(month=q_start_month, day=1), today_end
    if period == "half":
        h_start_month = 1 if today_start.month <= 6 else 7
        return today_start.replace(month=h_start_month, day=1), today_end
    if period == "year":
        return today_start.replace(month=1, day=1), today_end
    return today_start - timedelta(days=6), today_end


def _ledger_totals(start, end):
    row = (
        TransactionLedger.objects.filter(created_at__gte=start, created_at__lte=end)
        .aggregate(
            paid=Sum(
                "store_payout_amount",
                filter=Q(payout_status=TransactionLedger.PayoutStatus.PAID),
            ),
            held=Sum(
                "store_payout_amount",
                filter=Q(payout_status=TransactionLedger.PayoutStatus.HELD),
            ),
        )
    )
    return float(row["paid"] or 0), float(row["held"] or 0)


def aggregate_range(start, end, rate=None):
    if rate is None:
        rate = PlatformCommissionRate.get_rate()

    agg = revenue_queryset().filter(
        created_at__gte=start, created_at__lte=end
    ).aggregate(
        gross=Sum("grand_total_sar"),
        order_count=Count("id"),
        completed_count=Count("id", filter=Q(status=Order.Status.COMPLETED)),
    )
    gross_sales = round(float(agg["gross"] or 0), 2)
    commission_sar = round(gross_sales * rate, 2)
    vat_collected_sar = round(gross_sales * 0.15, 2)

    paid_total, held_total = _ledger_totals(start, end)
    net_payout_sar = paid_total or round(gross_sales - commission_sar, 2)

    return {
        "grossSalesSar": gross_sales,
        "commissionSar": commission_sar,
        "vatCollectedSar": vat_collected_sar,
        "netPayoutSar": round(net_payout_sar, 2),
        "escrowHeldSar": round(held_total, 2),
        "orderCount": int(agg["order_count"] or 0),
        "completedCount": int(agg["completed_count"] or 0),
        "commissionRate": rate,
    }


def month_rollups(start, end, rate=None):
    if rate is None:
        rate = PlatformCommissionRate.get_rate()

    order_rows = (
        revenue_queryset()
        .filter(created_at__gte=start, created_at__lte=end)
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(
            gross=Sum("grand_total_sar"),
            order_count=Count("id"),
            completed_count=Count("id", filter=Q(status=Order.Status.COMPLETED)),
        )
    )
    by_month = {row["month"]: row for row in order_rows}

    ledger_rows = (
        TransactionLedger.objects.filter(created_at__gte=start, created_at__lte=end)
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(
            paid=Sum(
                "store_payout_amount",
                filter=Q(payout_status=TransactionLedger.PayoutStatus.PAID),
            ),
            held=Sum(
                "store_payout_amount",
                filter=Q(payout_status=TransactionLedger.PayoutStatus.HELD),
            ),
        )
    )
    ledger_by_month = {row["month"]: row for row in ledger_rows}

    rollups = []
    cursor = start.replace(day=1)
    end_date = timezone.localdate(end)
    while cursor.date() <= end_date:
        next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_end = min(next_month - timedelta(microseconds=1), end)
        month_start = cursor
        month_key = cursor.date().replace(day=1)

        order_row = by_month.get(month_key)
        gross_sales = round(float(order_row["gross"] or 0), 2) if order_row else 0.0
        commission_sar = round(gross_sales * rate, 2)

        ledger_row = ledger_by_month.get(month_key)
        paid_total = float(ledger_row["paid"] or 0) if ledger_row else 0.0
        held_total = float(ledger_row["held"] or 0) if ledger_row else 0.0

        rollups.append(
            {
                "label": cursor.strftime("%B %Y"),
                "start": month_start.isoformat(),
                "end": month_end.isoformat(),
                "grossSalesSar": gross_sales,
                "commissionSar": commission_sar,
                "vatCollectedSar": round(gross_sales * 0.15, 2),
                "netPayoutSar": round(
                    paid_total or round(gross_sales - commission_sar, 2), 2
                ),
                "escrowHeldSar": round(held_total, 2),
                "orderCount": int(order_row["order_count"] or 0) if order_row else 0,
                "completedCount": (
                    int(order_row["completed_count"] or 0) if order_row else 0
                ),
                "commissionRate": rate,
            }
        )
        cursor = next_month
    return rollups


def accrual_breakdown(start, end):
    row = revenue_queryset().filter(
        created_at__gte=start, created_at__lte=end
    ).aggregate(
        cash_amount=Sum(
            "grand_total_sar", filter=Q(payment_status=Order.PaymentStatus.PAID)
        ),
        cash_count=Count("id", filter=Q(payment_status=Order.PaymentStatus.PAID)),
        completed_paid_amount=Sum(
            "grand_total_sar",
            filter=Q(
                status=Order.Status.COMPLETED,
                payment_status=Order.PaymentStatus.PAID,
            ),
        ),
        completed_paid_count=Count(
            "id",
            filter=Q(
                status=Order.Status.COMPLETED,
                payment_status=Order.PaymentStatus.PAID,
            ),
        ),
        credit_amount=Sum(
            "grand_total_sar",
            filter=Q(
                status=Order.Status.COMPLETED,
                payment_status=Order.PaymentStatus.UNPAID,
            ),
        ),
        credit_count=Count(
            "id",
            filter=Q(
                status=Order.Status.COMPLETED,
                payment_status=Order.PaymentStatus.UNPAID,
            ),
        ),
        earned_amount=Sum(
            "grand_total_sar", filter=Q(status=Order.Status.COMPLETED)
        ),
    )

    cash_amount = float(row["cash_amount"] or 0)
    cash_count = int(row["cash_count"] or 0)
    completed_paid_amount = float(row["completed_paid_amount"] or 0)
    completed_paid_count = int(row["completed_paid_count"] or 0)

    return {
        "cashCollectedSar": round(cash_amount, 2),
        "creditSalesSar": round(float(row["credit_amount"] or 0), 2),
        "deferredRevenueSar": round(cash_amount - completed_paid_amount, 2),
        "earnedRevenueSar": round(float(row["earned_amount"] or 0), 2),
        "cashOrdersCount": cash_count,
        "creditOrdersCount": int(row["credit_count"] or 0),
        "deferredOrdersCount": cash_count - completed_paid_count,
    }


def daily_rows(start, end, rate=None):
    if rate is None:
        rate = PlatformCommissionRate.get_rate()

    rows = (
        revenue_queryset()
        .filter(created_at__gte=start, created_at__lte=end)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(gross=Sum("grand_total_sar"), orders=Count("id"))
        .order_by("day")
    )
    by_day = {row["day"]: row for row in rows}

    start_date = timezone.localdate(start)
    end_date = timezone.localdate(end)
    day_count = (end_date - start_date).days
    daily = []
    for offset in range(day_count + 1):
        day = start_date + timedelta(days=offset)
        row = by_day.get(day)
        gross = float(row["gross"] or 0) if row else 0.0
        orders = row["orders"] if row else 0
        daily.append(
            {
                "date": day.isoformat(),
                "grossSalesSar": round(gross, 2),
                "commissionSar": round(gross * rate, 2),
                "ordersCount": orders,
            }
        )
    return daily


def accounting_payload(period):
    if period not in ("day", "week", "month", "quarter", "half", "year"):
        period = "month"
    now = timezone.now()
    start, end = period_range(period, now)
    rate = PlatformCommissionRate.get_rate()
    totals = aggregate_range(start, end, rate)
    accrual = accrual_breakdown(start, end)
    daily = daily_rows(start, end, rate)
    rollups = []
    if period in ("month", "quarter", "half", "year"):
        rollups = month_rollups(start, end, rate)
    return {
        "period": period,
        "rangeStart": start.isoformat(),
        "rangeEnd": end.isoformat(),
        "totals": totals,
        "accrual": accrual,
        "daily": daily,
        "rollups": rollups,
    }
