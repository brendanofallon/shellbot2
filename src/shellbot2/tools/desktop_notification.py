"""Send native desktop notifications and optionally collect a text reply."""

import asyncio
from collections.abc import Callable
import platform
from typing import Any

from desktop_notifier import (
    DEFAULT_SOUND,
    Capability,
    DesktopNotifier,
    ReplyField,
    Urgency,
)

from shellbot2.tools.tool_spec import ToolSpec


NotificationResult = dict[str, str | None]


class DesktopNotificationTool:
    """Model-facing tool backed by ``desktop-notifier``."""

    def __init__(
        self,
        app_name: str = "ShellBot2",
        notification_limit: int | None = None,
        notifier_factory: Callable[..., DesktopNotifier] | None = None,
    ) -> None:
        self.app_name = app_name
        self.notification_limit = notification_limit
        self._notifier_factory = notifier_factory or DesktopNotifier

    async def __call__(
        self,
        *,
        title: str,
        message: str,
        urgency: str = "normal",
        reply_prompt: str | None = None,
        reply_button_label: str = "Send",
        reply_timeout_seconds: float = 120,
        notification_timeout_seconds: int = -1,
        thread: str | None = None,
        sound: bool = False,
    ) -> NotificationResult:
        """Send a notification and, when requested, wait for its text response."""

        self._validate_arguments(
            title=title,
            message=message,
            urgency=urgency,
            reply_prompt=reply_prompt,
            reply_button_label=reply_button_label,
            reply_timeout_seconds=reply_timeout_seconds,
            notification_timeout_seconds=notification_timeout_seconds,
            thread=thread,
            sound=sound,
        )
        return await asyncio.to_thread(
            self._run_notification_loop,
            title=title,
            message=message,
            urgency=urgency,
            reply_prompt=reply_prompt,
            reply_button_label=reply_button_label,
            reply_timeout_seconds=reply_timeout_seconds,
            notification_timeout_seconds=notification_timeout_seconds,
            thread=thread,
            sound=sound,
        )

    def _run_notification_loop(self, **kwargs: Any) -> NotificationResult:
        """Run a dedicated event loop so native callbacks remain active."""

        loop = self._create_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(self._send_notification(**kwargs))
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    @staticmethod
    def _create_event_loop() -> asyncio.AbstractEventLoop:
        if platform.system() == "Darwin":
            # macOS notification callbacks require a running Core Foundation loop.
            from rubicon.objc.eventloop import CFEventLoop

            return CFEventLoop()
        return asyncio.new_event_loop()

    async def _send_notification(
        self,
        *,
        title: str,
        message: str,
        urgency: str,
        reply_prompt: str | None,
        reply_button_label: str,
        reply_timeout_seconds: float,
        notification_timeout_seconds: int,
        thread: str | None,
        sound: bool,
    ) -> NotificationResult:
        notifier = self._notifier_factory(
            app_name=self.app_name,
            notification_limit=self.notification_limit,
        )
        reply_supported = (
            reply_prompt is None
            or Capability.REPLY_FIELD in await notifier.get_capabilities()
        )
        response_future: asyncio.Future[NotificationResult] | None = None
        reply_field: ReplyField | None = None
        on_dismissed = None

        if reply_prompt is not None and reply_supported:
            response_future = asyncio.get_running_loop().create_future()

            def resolve_response(status: str, response: str | None) -> None:
                if not response_future.done():
                    response_future.set_result({"status": status, "response": response})

            def on_replied(response: str) -> None:
                response_future.get_loop().call_soon_threadsafe(
                    resolve_response,
                    "replied",
                    response,
                )

            def on_dismissed_callback() -> None:
                response_future.get_loop().call_soon_threadsafe(
                    resolve_response,
                    "dismissed",
                    None,
                )

            reply_field = ReplyField(
                title=reply_prompt,
                button_title=reply_button_label,
                on_replied=on_replied,
            )
            on_dismissed = on_dismissed_callback

        notification_id = await notifier.send(
            title=title,
            message=message,
            urgency=Urgency(urgency),
            reply_field=reply_field,
            on_dismissed=on_dismissed,
            sound=DEFAULT_SOUND if sound else None,
            thread=thread,
            timeout=notification_timeout_seconds,
        )
        if not reply_supported:
            return {
                "notification_id": notification_id,
                "status": "reply_not_supported",
                "response": None,
            }
        if response_future is None:
            return {
                "notification_id": notification_id,
                "status": "sent",
                "response": None,
            }

        try:
            result = await asyncio.wait_for(
                asyncio.shield(response_future),
                timeout=reply_timeout_seconds,
            )
        except asyncio.TimeoutError:
            response_future.cancel()
            result = {"status": "reply_timed_out", "response": None}
        return {"notification_id": notification_id, **result}

    @staticmethod
    def _validate_arguments(
        *,
        title: Any,
        message: Any,
        urgency: Any,
        reply_prompt: Any,
        reply_button_label: Any,
        reply_timeout_seconds: Any,
        notification_timeout_seconds: Any,
        thread: Any,
        sound: Any,
    ) -> None:
        if not isinstance(title, str) or not title:
            raise ValueError("title must be a non-empty string")
        if not isinstance(message, str):
            raise ValueError("message must be a string")
        if urgency not in {item.value for item in Urgency}:
            raise ValueError("urgency must be one of: low, normal, critical")
        if reply_prompt is not None and (not isinstance(reply_prompt, str) or not reply_prompt):
            raise ValueError("reply_prompt must be a non-empty string when provided")
        if not isinstance(reply_button_label, str) or not reply_button_label:
            raise ValueError("reply_button_label must be a non-empty string")
        if (
            isinstance(reply_timeout_seconds, bool)
            or not isinstance(reply_timeout_seconds, int | float)
            or reply_timeout_seconds <= 0
        ):
            raise ValueError("reply_timeout_seconds must be a positive number")
        if (
            isinstance(notification_timeout_seconds, bool)
            or not isinstance(notification_timeout_seconds, int)
            or notification_timeout_seconds < -1
        ):
            raise ValueError("notification_timeout_seconds must be an integer greater than or equal to -1")
        if thread is not None and not isinstance(thread, str):
            raise ValueError("thread must be a string when provided")
        if not isinstance(sound, bool):
            raise ValueError("sound must be a boolean")


TOOL_SPECS = (
    ToolSpec(
        name="desktop-notification",
        function_name="send_desktop_notification",
        description=(
            "Send a native desktop notification. Optionally include a text reply "
            "field and return the user's response, dismissal, reply timeout, or "
            "unsupported-reply status."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Notification title.",
                },
                "message": {
                    "type": "string",
                    "description": "Notification body text.",
                },
                "urgency": {
                    "type": "string",
                    "description": "Optional notification urgency.",
                    "enum": ["low", "normal", "critical"],
                    "default": "normal",
                },
                "reply_prompt": {
                    "type": "string",
                    "description": (
                        "Optional label for a native text-reply field. When set, "
                        "the tool waits for the user's reply, dismissal, or timeout "
                        "when the platform supports native replies."
                    ),
                },
                "reply_button_label": {
                    "type": "string",
                    "description": "Optional label for the reply submit button.",
                    "default": "Send",
                },
                "reply_timeout_seconds": {
                    "type": "number",
                    "description": (
                        "Maximum seconds to wait for a reply when reply_prompt is "
                        "set. Defaults to 120."
                    ),
                    "default": 120,
                    "minimum": 1,
                },
                "notification_timeout_seconds": {
                    "type": "integer",
                    "description": (
                        "Optional native notification display timeout in seconds; "
                        "-1 uses the platform default."
                    ),
                    "default": -1,
                    "minimum": -1,
                },
                "thread": {
                    "type": "string",
                    "description": "Optional native notification thread/group identifier.",
                },
                "sound": {
                    "type": "boolean",
                    "description": "Play the platform default notification sound.",
                    "default": False,
                },
            },
            "required": ["title", "message"],
        },
        factory=lambda _runtime, kwargs: DesktopNotificationTool(**kwargs),
    ),
)
