"""AppKit lifecycle support for the frozen macOS application."""

import asyncio
from collections.abc import Awaitable, Callable
import logging
import signal
from typing import Any


logger = logging.getLogger(__name__)

_TERMINATE_NOW = 1
_TERMINATE_LATER = 2

CliMain = Callable[..., Awaitable[None]]


def _ensure_appkit_loaded() -> None:
    """Load AppKit before Rubicon looks up Cocoa classes.

    ``rubicon.objc.eventloop`` resolves ``NSEvent`` at import time. That class
    lives in AppKit, which is not loaded automatically in a frozen app (or a
    plain Python process that has not imported AppKit yet).
    """

    from rubicon.objc.runtime import load_library

    load_library("AppKit")


def run_daemon_application(cli_main: CliMain) -> None:
    """Run the CLI in an NSApplication lifecycle with graceful quit support."""

    _ensure_appkit_loaded()

    from rubicon.objc import NSObject, ObjCClass, objc_method
    from rubicon.objc.eventloop import CocoaLifecycle, RubiconEventLoop

    ns_application = ObjCClass("NSApplication")
    ns_application.declare_class_property("sharedApplication")
    application = ns_application.sharedApplication
    application.setActivationPolicy_(0)  # NSApplicationActivationPolicyRegular

    loop = RubiconEventLoop()
    asyncio.set_event_loop(loop)

    daemon: Any | None = None
    main_task: asyncio.Task[None] | None = None
    shutdown_task: asyncio.Task[None] | None = None
    termination_requested = False

    def set_daemon(daemon_instance: Any) -> None:
        nonlocal daemon
        daemon = daemon_instance

    async def shutdown_daemon() -> None:
        if daemon is not None:
            try:
                await daemon.stop()
            except Exception:
                logger.exception("Error while stopping the daemon for application quit")

        if main_task is not None and not main_task.done():
            main_task.cancel()

    def request_termination() -> bool:
        nonlocal shutdown_task, termination_requested

        if main_task is None or main_task.done():
            return True
        if termination_requested:
            return False

        termination_requested = True
        shutdown_task = loop.create_task(shutdown_daemon())
        return False

    class ApplicationDelegate(NSObject):
        @objc_method
        def applicationShouldTerminate_(self, _sender) -> int:
            if request_termination():
                return _TERMINATE_NOW
            return _TERMINATE_LATER

    delegate = ApplicationDelegate.alloc().init()
    application.setDelegate_(delegate)

    main_task = loop.create_task(cli_main(on_daemon_ready=set_daemon))

    def complete_termination(_task: asyncio.Task[None]) -> None:
        if termination_requested:
            application.replyToApplicationShouldTerminate_(True)

    main_task.add_done_callback(complete_termination)

    def terminate_from_signal() -> None:
        application.terminate_(None)

    loop.add_signal_handler(signal.SIGINT, terminate_from_signal)
    loop.add_signal_handler(signal.SIGTERM, terminate_from_signal)

    try:
        loop.run_until_complete(main_task, lifecycle=CocoaLifecycle(application))
    except asyncio.CancelledError:
        if not termination_requested:
            raise
    finally:
        loop.remove_signal_handler(signal.SIGINT)
        loop.remove_signal_handler(signal.SIGTERM)
        if shutdown_task is not None and not shutdown_task.done():
            shutdown_task.cancel()
        loop.close()
        asyncio.set_event_loop(None)
