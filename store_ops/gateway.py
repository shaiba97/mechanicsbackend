"""Gateway integration for store payouts.

This module is the integration point with STC Pay / Mada / Visa / MasterCard /
Apple Pay acquiring gateways. Until the bank API credentials are provisioned,
execute_iban_transfer() simulates a successful transfer so the admin panel and
celery task can be tested end-to-end.
"""

import uuid


def execute_iban_transfer(
    from_iban: str,
    to_iban: str,
    amount,
    reference: str,
) -> dict:
    """Execute an IBAN transfer from the platform account to a store account.

    Returns a dict with keys:
        success (bool)
        transfer_id (str | None)
        error (str | None)
    """
    # TODO: Replace with real STC Pay / Mada / bank gateway integration once
    # API credentials are provisioned in PlatformGatewayConfig.
    #
    # Example integration shape:
    #     response = requests.post(
    #         f"{GATEWAY_BASE_URL}/v1/transfers",
    #         headers={"Authorization": f"Bearer {ACQUIRER_API_KEY}"},
    #         json={
    #             "source_iban": from_iban,
    #             "destination_iban": to_iban,
    #             "amount_sar": str(amount),
    #             "reference": reference,
    #         },
    #         timeout=30,
    #     )
    #     response.raise_for_status()
    #     data = response.json()
    #     return {"success": True, "transfer_id": data["transfer_id"], "error": None}

    return {
        "success": True,
        "transfer_id": f"TRF-{uuid.uuid4().hex[:12]}",
        "error": None,
    }
