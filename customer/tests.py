"""Customer realtime order WebSocket tests.

Run with: python manage.py test customer --settings=backend.settings_test
"""

from channels.testing import WebsocketCommunicator
from django.test import TestCase

from authentication.models import User
from authentication.tokens import issue_access_token
from backend.asgi import application


class CustomerOrdersConsumerTest(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            email="buyer@example.com", password="pw123456", role="CUSTOMER"
        )
        self.other_customer = User.objects.create_user(
            email="other@example.com", password="pw123456", role="CUSTOMER"
        )

    async def _connect(self, user, order_id):
        token = issue_access_token(user)
        communicator = WebsocketCommunicator(
            application,
            f"/ws/customer/orders/{order_id}/?token={token}",
        )
        connected, _ = await communicator.connect()
        return communicator, connected

    @staticmethod
    def _run(coro):
        import asyncio

        return asyncio.get_event_loop().run_until_complete(coro)

    def test_valid_customer_connects_and_receives_connected(self):
        communicator, connected = self._run(
            self._connect(self.customer, "00000000-0000-0000-0000-000000000001")
        )
        self.assertTrue(connected)
        message = self._run(communicator.receive_json_from())
        self.assertEqual(
            message,
            {"type": "order:connected", "orderId": "00000000-0000-0000-0000-000000000001"},
        )
        communicator.disconnect()

    def test_missing_token_is_rejected_4401(self):
        communicator = WebsocketCommunicator(
            application,
            "/ws/customer/orders/00000000-0000-0000-0000-000000000001/",
        )
        connected, _ = self._run(communicator.connect())
        self.assertFalse(connected)

    def test_refresh_token_is_rejected_4403(self):
        from authentication.tokens import issue_refresh_token

        token = issue_refresh_token(self.customer)
        communicator = WebsocketCommunicator(
            application,
            f"/ws/customer/orders/00000000-0000-0000-0000-000000000001/?token={token}",
        )
        connected, _ = self._run(communicator.connect())
        self.assertFalse(connected)

    def test_ping_pong(self):
        communicator, _ = self._run(
            self._connect(self.customer, "00000000-0000-0000-0000-000000000001")
        )
        # The connect() handshake message is already in the queue.
        self._run(communicator.receive_json_from())
        self._run(communicator.send_json_to({"type": "ping"}))
        message = self._run(communicator.receive_json_from())
        self.assertEqual(message, {"type": "pong"})
        communicator.disconnect()
