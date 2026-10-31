import json
import unittest
from unittest.mock import patch

from chat_store import ChatStore, ChatStoreError, create_session


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class ChatStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = ChatStore("https://example.supabase.co", "public-key")
        self.session = {
            "access_token": "user-access-token",
            "refresh_token": "refresh-token",
            "user_id": "user-123",
            "email": "traveler@example.com",
        }

    @patch("chat_store.urlopen")
    def test_insert_sends_user_jwt_and_returns_conversation_id(self, mock_urlopen):
        mock_urlopen.return_value = FakeResponse(b'[{"id":"conversation-123"}]')
        conversation_id = self.store.save_conversation(
            self.session, None, "Thailand", [{"role": "user", "content": "Thailand"}]
        )

        request = mock_urlopen.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(conversation_id, "conversation-123")
        self.assertEqual(request.get_header("Authorization"), "Bearer user-access-token")
        self.assertEqual(body["user_id"], "user-123")
        self.assertEqual(body["title"], "Thailand")
        self.assertEqual(body["messages"][0]["content"], "Thailand")

    @patch("chat_store.urlopen")
    def test_update_filters_conversation_by_owner(self, mock_urlopen):
        mock_urlopen.return_value = FakeResponse(b"")
        conversation_id = self.store.save_conversation(
            self.session, "conversation-123", "Updated", []
        )

        request = mock_urlopen.call_args.args[0]
        self.assertEqual(conversation_id, "conversation-123")
        self.assertIn("id=eq.conversation-123", request.full_url)
        self.assertIn("user_id=eq.user-123", request.full_url)

    def test_incomplete_auth_response_requires_email_confirmation(self):
        with self.assertRaisesRegex(ChatStoreError, "confirm your account"):
            create_session({"user": {"id": "user-123"}})

    @patch("chat_store.urlopen")
    def test_public_auth_request_does_not_send_a_user_authorization_header(
        self, mock_urlopen
    ):
        mock_urlopen.return_value = FakeResponse(b'{"user":{}}')
        self.store.sign_up("traveler@example.com", "password")

        request = mock_urlopen.call_args.args[0]
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(request.get_header("Apikey"), "public-key")

    def test_auth_response_creates_user_session(self):
        session = create_session(
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
                "user": {"id": "user-123", "email": "traveler@example.com"},
            }
        )
        self.assertEqual(session["user_id"], "user-123")
        self.assertEqual(session["refresh_token"], "refresh")
        self.assertEqual(session["email"], "traveler@example.com")


if __name__ == "__main__":
    unittest.main()
