import asyncio
import threading

import pytest

from shellbot2.agent import safe_tool_call
from shellbot2.tools.desktop_notification import DesktopNotificationTool, TOOL_SPECS
from desktop_notifier import Capability


class FakeNotifier:
    def __init__(self, on_send=None, capabilities=frozenset({Capability.REPLY_FIELD})):
        self.on_send = on_send
        self.capabilities = capabilities
        self.send_kwargs = None
        self.send_thread_id = None

    async def get_capabilities(self):
        return self.capabilities

    async def send(self, **kwargs):
        self.send_thread_id = threading.get_ident()
        self.send_kwargs = kwargs
        if self.on_send is not None:
            self.on_send(kwargs)
        return "notification-123"


def make_tool(notifier: FakeNotifier) -> DesktopNotificationTool:
    return DesktopNotificationTool(
        app_name="ShellBot2 tests",
        notification_limit=3,
        notifier_factory=lambda **_kwargs: notifier,
    )


def test_sends_notification_with_requested_options():
    notifier = FakeNotifier()
    tool = make_tool(notifier)
    calling_thread_id = threading.get_ident()

    result = asyncio.run(
        tool(
            title="Backup complete",
            message="All files were saved.",
            urgency="low",
            thread="backups",
            sound=True,
            notification_timeout_seconds=15,
        )
    )

    assert result == {
        "notification_id": "notification-123",
        "status": "sent",
        "response": None,
    }
    assert notifier.send_kwargs["title"] == "Backup complete"
    assert notifier.send_kwargs["message"] == "All files were saved."
    assert notifier.send_kwargs["urgency"].value == "low"
    assert notifier.send_kwargs["thread"] == "backups"
    assert notifier.send_kwargs["timeout"] == 15
    assert notifier.send_kwargs["sound"] is not None
    assert notifier.send_thread_id == calling_thread_id


def test_treats_blank_reply_prompt_as_omitted():
    notifier = FakeNotifier()

    result = asyncio.run(
        make_tool(notifier)(
            title="Backup complete",
            message="All files were saved.",
            reply_prompt="   ",
        )
    )

    assert result == {
        "notification_id": "notification-123",
        "status": "sent",
        "response": None,
    }
    assert notifier.send_kwargs["reply_field"] is None


def test_returns_text_response_from_notification():
    def reply_to_notification(send_kwargs):
        send_kwargs["reply_field"].on_replied("Yes, deploy it.")

    notifier = FakeNotifier(on_send=reply_to_notification)
    tool = make_tool(notifier)

    result = asyncio.run(
        tool(
            title="Deploy?",
            message="The checks passed.",
            reply_prompt="Your decision",
            reply_button_label="Submit",
        )
    )

    assert result == {
        "notification_id": "notification-123",
        "status": "replied",
        "response": "Yes, deploy it.",
    }
    assert notifier.send_kwargs["reply_field"].title == "Your decision"
    assert notifier.send_kwargs["reply_field"].button_title == "Submit"


def test_returns_unsupported_status_without_waiting_for_an_impossible_reply():
    notifier = FakeNotifier(capabilities=frozenset())
    tool = make_tool(notifier)

    result = asyncio.run(
        tool(
            title="Respond",
            message="Can you reply?",
            reply_prompt="Your response",
        )
    )

    assert result == {
        "notification_id": "notification-123",
        "status": "reply_not_supported",
        "response": None,
    }
    assert notifier.send_kwargs["reply_field"] is None


def test_returns_timeout_when_reply_is_not_received():
    notifier = FakeNotifier()
    tool = make_tool(notifier)

    result = asyncio.run(
        tool(
            title="Waiting",
            message="Please respond.",
            reply_prompt="Reply",
            reply_timeout_seconds=0.01,
        )
    )

    assert result == {
        "notification_id": "notification-123",
        "status": "reply_timed_out",
        "response": None,
    }


def test_rejects_invalid_notification_arguments():
    tool = make_tool(FakeNotifier())

    with pytest.raises(ValueError, match="urgency"):
        asyncio.run(tool(title="Test", message="Body", urgency="high"))

    with pytest.raises(ValueError, match="reply_timeout_seconds"):
        asyncio.run(tool(title="Test", message="Body", reply_timeout_seconds=0))


def test_spec_exposes_model_facing_notification_tool():
    spec = TOOL_SPECS[0]

    assert spec.name == "desktop-notifier"
    assert spec.model_name == "send_desktop_notification"
    assert spec.parameters["required"] == ["title", "message"]


def test_safe_tool_call_preserves_async_tools():
    async def async_tool(**kwargs):
        return kwargs["message"]

    wrapped = safe_tool_call(async_tool, "async-tool")

    assert asyncio.run(wrapped(message="done")) == "done"
