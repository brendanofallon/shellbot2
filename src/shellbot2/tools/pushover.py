"""Send push notifications through the Pushover Message API."""

import asyncio
from collections.abc import Mapping
import json
import os
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from shellbot2.tools.tool_spec import ToolSpec


PUSHOVER_MESSAGES_URL = "https://api.pushover.net/1/messages.json"
PUSHOVER_BUILT_IN_SOUNDS = (
    "pushover",
    "bike",
    "bugle",
    "cashregister",
    "classical",
    "cosmic",
    "falling",
    "gamelan",
    "incoming",
    "intermission",
    "magic",
    "mechanical",
    "pianobar",
    "siren",
    "spacealarm",
    "tugboat",
    "alien",
    "climb",
    "persistent",
    "echo",
    "updown",
    "vibrate",
    "none",
)


class PushoverAPIError(RuntimeError):
    """An error response returned by Pushover."""

    def __init__(self, status_code: int, errors: list[str], request_id: str | None) -> None:
        message = "; ".join(errors) if errors else "Unknown Pushover API error"
        if request_id:
            message = f"{message} (request ID: {request_id})"
        super().__init__(f"Pushover request failed ({status_code}): {message}")


class PushoverNotificationTool:
    """Model-facing tool that posts notifications to Pushover."""

    def __init__(
        self,
        user_key: str | None = None,
        app_token: str | None = None,
        request_timeout_seconds: float = 10,
    ) -> None:
        self.user_key = user_key or os.getenv("PUSHOVER_USER_KEY")
        self.app_token = app_token or os.getenv("PUSHOVER_APP_TOKEN")
        if not self.user_key:
            raise ValueError(
                "Pushover user key is required. Set PUSHOVER_USER_KEY or configure user_key."
            )
        if not self.app_token:
            raise ValueError(
                "Pushover app token is required. Set PUSHOVER_APP_TOKEN or configure app_token."
            )
        if (
            isinstance(request_timeout_seconds, bool)
            or not isinstance(request_timeout_seconds, int | float)
            or request_timeout_seconds <= 0
        ):
            raise ValueError("request_timeout_seconds must be a positive number")
        self.request_timeout_seconds = request_timeout_seconds

    async def __call__(
        self,
        *,
        message: str,
        title: str | None = None,
        device: str | None = None,
        priority: int = 0,
        sound: str | None = None,
        html: bool = False,
        monospace: bool = False,
        timestamp: int | None = None,
        ttl: int | None = None,
        url: str | None = None,
        url_title: str | None = None,
    ) -> dict[str, Any]:
        """Send a Pushover notification and return Pushover's delivery metadata."""

        if isinstance(device, str) and not device.strip():
            device = None
        self._validate_arguments(
            message=message,
            title=title,
            device=device,
            priority=priority,
            sound=sound,
            html=html,
            monospace=monospace,
            timestamp=timestamp,
            ttl=ttl,
            url=url,
            url_title=url_title,
        )
        payload = {
            "token": self.app_token,
            "user": self.user_key,
            "message": message,
            "priority": priority,
        }
        optional_fields = {
            "title": title,
            "device": device,
            "sound": sound,
            "timestamp": timestamp,
            "ttl": ttl,
            "url": url,
            "url_title": url_title,
        }
        payload.update(
            {name: value for name, value in optional_fields.items() if value is not None}
        )
        if html:
            payload["html"] = 1
        if monospace:
            payload["monospace"] = 1

        return await asyncio.to_thread(self._send, payload)

    def _send(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        encoded_payload = urlencode(payload).encode("utf-8")
        request = Request(
            PUSHOVER_MESSAGES_URL,
            data=encoded_payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.request_timeout_seconds) as response:
                response_body = response.read()
                response_data = self._parse_response(response_body)
                status_code = response.status
                headers = response.headers
        except HTTPError as error:
            response_data = self._parse_response(error.read())
            errors = response_data.get("errors")
            if not isinstance(errors, list):
                errors = []
            raise PushoverAPIError(
                error.code,
                [str(item) for item in errors],
                response_data.get("request"),
            ) from error

        if status_code != 200 or response_data.get("status") != 1:
            errors = response_data.get("errors")
            if not isinstance(errors, list):
                errors = []
            raise PushoverAPIError(
                status_code,
                [str(item) for item in errors],
                response_data.get("request"),
            )

        result: dict[str, Any] = {
            "status": "sent",
            "request_id": response_data.get("request"),
        }
        for header_name, result_name in (
            ("X-Limit-App-Limit", "monthly_limit"),
            ("X-Limit-App-Remaining", "monthly_remaining"),
            ("X-Limit-App-Reset", "monthly_reset_timestamp"),
        ):
            if header_value := headers.get(header_name):
                result[result_name] = int(header_value)
        return result

    @staticmethod
    def _parse_response(response_body: bytes) -> dict[str, Any]:
        try:
            response_data = json.loads(response_body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RuntimeError("Pushover returned an invalid JSON response") from error
        if not isinstance(response_data, dict):
            raise RuntimeError("Pushover returned an invalid JSON response")
        return response_data

    @staticmethod
    def _validate_arguments(
        *,
        message: Any,
        title: Any,
        device: Any,
        priority: Any,
        sound: Any,
        html: Any,
        monospace: Any,
        timestamp: Any,
        ttl: Any,
        url: Any,
        url_title: Any,
    ) -> None:
        _validate_optional_string("title", title, max_length=250)
        _validate_string("message", message, max_length=1024)
        _validate_optional_string("device", device)
        _validate_optional_string("sound", sound)
        _validate_optional_string("url", url, max_length=512)
        _validate_optional_string("url_title", url_title, max_length=100)
        if url_title is not None and url is None:
            raise ValueError("url_title requires url")
        if isinstance(priority, bool) or priority not in {-2, -1, 0, 1}:
            raise ValueError("priority must be one of: -2, -1, 0, 1")
        if not isinstance(html, bool):
            raise ValueError("html must be a boolean")
        if not isinstance(monospace, bool):
            raise ValueError("monospace must be a boolean")
        if html and monospace:
            raise ValueError("html and monospace cannot both be enabled")
        _validate_optional_positive_integer("timestamp", timestamp)
        _validate_optional_positive_integer("ttl", ttl)



def _validate_string(name: str, value: Any, *, max_length: int | None = None) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    if max_length is not None and len(value) > max_length:
        raise ValueError(f"{name} must not exceed {max_length} characters")


def _validate_optional_string(
    name: str,
    value: Any,
    *,
    max_length: int | None = None,
) -> None:
    if value is not None:
        _validate_string(name, value, max_length=max_length)


def _validate_optional_positive_integer(name: str, value: Any) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
        raise ValueError(f"{name} must be a positive integer when provided")


TOOL_SPECS = (
    ToolSpec(
        name="pushover",
        function_name="send_pushover_notification",
        description=(
            "Send a push notification through Pushover. The user's configured "
            "Pushover sound is used unless a sound is specified."
        ),
        parameters={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Notification body, up to 1024 characters.",
                },
                "title": {
                    "type": "string",
                    "description": (
                        "Optional notification title, up to 250 characters. When "
                        "omitted, Pushover uses the application's configured name."
                    ),
                },
                "device": {
                    "type": "string",
                    "description": (
                        "Optional target device name or comma-separated device names. "
                        "Omit or leave blank to send to all devices."
                    ),
                },
                "priority": {
                    "type": "integer",
                    "description": (
                        "Notification priority: -2 (lowest), -1 (low), 0 (normal), "
                        "1 (high)."
                    ),
                    "enum": [-2, -1, 0, 1],
                    "default": 0,
                },
                "sound": {
                    "type": "string",
                    "description": (
                        "Optional Pushover sound name. Omit to use the user's default; "
                        f"built-in options include: {', '.join(PUSHOVER_BUILT_IN_SOUNDS)}. "
                        "Custom Pushover sounds are also accepted."
                    ),
                },
                "html": {
                    "type": "boolean",
                    "description": "Enable Pushover's supported HTML formatting.",
                    "default": False,
                },
                "monospace": {
                    "type": "boolean",
                    "description": "Render the message in monospace; cannot be combined with html.",
                    "default": False,
                },
                "timestamp": {
                    "type": "integer",
                    "description": "Optional original Unix timestamp to display for the message.",
                    "minimum": 1,
                },
                "ttl": {
                    "type": "integer",
                    "description": (
                        "Optional positive lifetime in seconds before the message is "
                        "deleted."
                    ),
                    "minimum": 1,
                    "default": 6*3600,
                },
                "url": {
                    "type": "string",
                    "description": "Optional supplementary URL shown below the notification.",
                },
                "url_title": {
                    "type": "string",
                    "description": "Optional display title for url; requires url.",
                },
            },
            "required": ["message"],
        },
        factory=lambda _runtime, kwargs: PushoverNotificationTool(**kwargs),
    ),
)
