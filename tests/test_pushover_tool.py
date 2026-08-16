import asyncio
import io
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs

import pytest

from shellbot2.tools import pushover
from shellbot2.tools.pushover import PushoverAPIError, PushoverNotificationTool, TOOL_SPECS


class FakeResponse:
    def __init__(self, body: dict, headers: dict[str, str] | None = None) -> None:
        self.body = json.dumps(body).encode()
        self.headers = headers or {}
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return self.body


def make_tool() -> PushoverNotificationTool:
    return PushoverNotificationTool(user_key="user-key", app_token="app-token")


def test_sends_high_priority_notification_with_custom_options(monkeypatch):
    response = FakeResponse(
        {"status": 1, "request": "request-123"},
        {
            "X-Limit-App-Limit": "10000",
            "X-Limit-App-Remaining": "9999",
            "X-Limit-App-Reset": "1735689600",
        },
    )
    received_requests = []

    def fake_urlopen(request, timeout):
        received_requests.append((request, timeout))
        return response

    monkeypatch.setattr(pushover, "urlopen", fake_urlopen)

    result = asyncio.run(
        make_tool()(
            message="The deployment needs attention.",
            title="Production alert",
            device="phone",
            priority=1,
            sound="custom-alert",
            html=True,
        )
    )

    assert result == {
        "status": "sent",
        "request_id": "request-123",
        "monthly_limit": 10000,
        "monthly_remaining": 9999,
        "monthly_reset_timestamp": 1735689600,
    }
    request, timeout = received_requests[0]
    assert request.full_url == pushover.PUSHOVER_MESSAGES_URL
    assert request.get_method() == "POST"
    assert timeout == 10
    assert parse_qs(request.data.decode()) == {
        "token": ["app-token"],
        "user": ["user-key"],
        "message": ["The deployment needs attention."],
        "title": ["Production alert"],
        "device": ["phone"],
        "priority": ["1"],
        "sound": ["custom-alert"],
        "html": ["1"],
    }


def test_omits_optional_values_to_use_pushover_defaults(monkeypatch):
    response = FakeResponse({"status": 1, "request": "request-123"})
    received_requests = []

    def fake_urlopen(request, timeout):
        received_requests.append((request, timeout))
        return response

    monkeypatch.setattr(pushover, "urlopen", fake_urlopen)

    result = asyncio.run(make_tool()(message="Backup finished."))

    assert result == {"status": "sent", "request_id": "request-123"}
    request, timeout = received_requests[0]
    assert timeout == 10
    assert parse_qs(request.data.decode()) == {
        "token": ["app-token"],
        "user": ["user-key"],
        "message": ["Backup finished."],
        "priority": ["0"],
    }


def test_omits_blank_url_fields(monkeypatch):
    response = FakeResponse({"status": 1, "request": "request-123"})
    received_requests = []

    def fake_urlopen(request, timeout):
        received_requests.append((request, timeout))
        return response

    monkeypatch.setattr(pushover, "urlopen", fake_urlopen)

    asyncio.run(
        make_tool()(
            message="Backup finished.",
            url="   ",
            url_title="",
        )
    )

    request, _ = received_requests[0]
    payload = parse_qs(request.data.decode())
    assert "url" not in payload
    assert "url_title" not in payload


def test_omits_blank_device_to_send_to_all_devices(monkeypatch):
    response = FakeResponse({"status": 1, "request": "request-123"})
    received_requests = []

    def fake_urlopen(request, timeout):
        received_requests.append((request, timeout))
        return response

    monkeypatch.setattr(pushover, "urlopen", fake_urlopen)

    asyncio.run(make_tool()(message="Backup finished.", device=""))

    request, _ = received_requests[0]
    assert "device" not in parse_qs(request.data.decode())


def test_returns_api_errors_without_exposing_credentials(monkeypatch):
    error_body = io.BytesIO(
        b'{"status": 0, "request": "request-123", "errors": ["user identifier is invalid"]}'
    )
    error = HTTPError(
        pushover.PUSHOVER_MESSAGES_URL,
        400,
        "Bad Request",
        hdrs=None,
        fp=error_body,
    )
    monkeypatch.setattr(pushover, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))

    with pytest.raises(
        PushoverAPIError,
        match=r"Pushover request failed \(400\): user identifier is invalid \(request ID: request-123\)",
    ) as exception:
        asyncio.run(make_tool()(message="Test"))

    assert "app-token" not in str(exception.value)
    assert "user-key" not in str(exception.value)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"priority": 2}, "priority must be one of"),
        ({"url_title": "Open dashboard"}, "url_title requires"),
        ({"html": True, "monospace": True}, "cannot both"),
        ({"sound": ""}, "sound must be a non-empty string"),
    ],
)
def test_rejects_invalid_notification_combinations(kwargs, error):
    with pytest.raises(ValueError, match=error):
        asyncio.run(make_tool()(message="Test", **kwargs))


def test_reads_credentials_from_environment(monkeypatch):
    monkeypatch.setenv("PUSHOVER_USER_KEY", "env-user-key")
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "env-app-token")

    tool = PushoverNotificationTool()

    assert tool.user_key == "env-user-key"
    assert tool.app_token == "env-app-token"


def test_requires_both_pushover_credentials(monkeypatch):
    monkeypatch.delenv("PUSHOVER_USER_KEY", raising=False)
    monkeypatch.delenv("PUSHOVER_APP_TOKEN", raising=False)

    with pytest.raises(ValueError, match="PUSHOVER_USER_KEY"):
        PushoverNotificationTool(app_token="app-token")
    with pytest.raises(ValueError, match="PUSHOVER_APP_TOKEN"):
        PushoverNotificationTool(user_key="user-key")


def test_spec_exposes_model_facing_pushover_tool():
    spec = TOOL_SPECS[0]

    assert spec.name == "pushover"
    assert spec.model_name == "send_pushover_notification"
    assert spec.parameters["required"] == ["message"]
    assert spec.parameters["properties"]["priority"]["default"] == 0
    assert spec.parameters["properties"]["priority"]["enum"] == [-2, -1, 0, 1]
    assert "default" not in spec.parameters["properties"]["device"]
    assert "default" not in spec.parameters["properties"]["sound"]
