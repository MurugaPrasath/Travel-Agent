"""Supabase Auth and per-user conversation storage using the REST API."""
import json
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ChatStoreError(RuntimeError):
    """Raised when authentication or conversation storage fails."""


class ChatStore:
    def __init__(self, url: str, anon_key: str):
        self.url = url.rstrip("/")
        self.anon_key = anon_key

    def sign_up(self, email: str, password: str) -> dict:
        return self._request(
            "POST", "/auth/v1/signup", body={"email": email, "password": password}
        )

    def sign_in(self, email: str, password: str) -> dict:
        return self._request(
            "POST",
            "/auth/v1/token?grant_type=password",
            body={"email": email, "password": password},
        )

    def refresh(self, refresh_token: str) -> dict:
        return self._request(
            "POST",
            "/auth/v1/token?grant_type=refresh_token",
            body={"refresh_token": refresh_token},
        )

    def sign_out(self, session: dict) -> None:
        self._request("POST", "/auth/v1/logout", session=session)

    def list_conversations(self, session: dict) -> list[dict]:
        query = urlencode(
            {"select": "id,title,updated_at", "order": "updated_at.desc"}
        )
        return self._request(
            "GET", f"/rest/v1/conversations?{query}", session=session
        )

    def get_conversation(self, session: dict, conversation_id: str) -> dict:
        query = urlencode(
            {
                "id": f"eq.{conversation_id}",
                "user_id": f"eq.{session['user_id']}",
                "select": "id,title,messages",
            }
        )
        rows = self._request(
            "GET", f"/rest/v1/conversations?{query}", session=session
        )
        if not rows:
            raise ChatStoreError("That conversation was not found for this account.")
        return rows[0]

    def save_conversation(
        self,
        session: dict,
        conversation_id: str | None,
        title: str,
        messages: list[dict],
    ) -> str:
        body = {
            "title": title,
            "messages": messages,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if conversation_id is None:
            body["user_id"] = session["user_id"]
            rows = self._request(
                "POST",
                "/rest/v1/conversations",
                session=session,
                body=body,
                prefer="return=representation",
            )
            if not rows:
                raise ChatStoreError("The conversation was not saved.")
            return rows[0]["id"]

        query = urlencode(
            {
                "id": f"eq.{conversation_id}",
                "user_id": f"eq.{session['user_id']}",
            }
        )
        self._request(
            "PATCH",
            f"/rest/v1/conversations?{query}",
            session=session,
            body=body,
        )
        return conversation_id

    def delete_conversation(self, session: dict, conversation_id: str) -> None:
        query = urlencode(
            {
                "id": f"eq.{conversation_id}",
                "user_id": f"eq.{session['user_id']}",
            }
        )
        self._request(
            "DELETE", f"/rest/v1/conversations?{query}", session=session
        )

    def _request(
        self,
        method: str,
        path: str,
        session: dict | None = None,
        body: dict | None = None,
        prefer: str | None = None,
    ):
        headers = {
            "apikey": self.anon_key,
            "Content-Type": "application/json",
        }
        if session:
            headers["Authorization"] = f"Bearer {session['access_token']}"
        if prefer:
            headers["Prefer"] = prefer
        request = Request(
            f"{self.url}{path}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=20) as response:
                payload = response.read()
        except HTTPError as error:
            try:
                error_body = json.loads(error.read())
                details = (
                    error_body.get("msg")
                    or error_body.get("message")
                    or error_body.get("error_description")
                )
            except (ValueError, AttributeError):
                details = None
            raise ChatStoreError(
                details or f"Supabase request failed (HTTP {error.code})."
            ) from None
        except (URLError, TimeoutError) as error:
            raise ChatStoreError(f"Could not reach Supabase: {error}") from None
        if not payload:
            return None
        try:
            return json.loads(payload)
        except (ValueError, TypeError) as error:
            raise ChatStoreError("Supabase returned an invalid response.") from error


def create_session(auth_response: dict) -> dict:
    user = auth_response.get("user") or {}
    if (
        not auth_response.get("access_token")
        or not auth_response.get("refresh_token")
        or not user.get("id")
    ):
        raise ChatStoreError(
            "Check your email to confirm your account, then sign in."
        )
    return {
        "access_token": auth_response["access_token"],
        "refresh_token": auth_response["refresh_token"],
        "expires_at": int(time.time()) + int(auth_response.get("expires_in", 3600)),
        "user_id": user["id"],
        "email": user.get("email", ""),
    }
