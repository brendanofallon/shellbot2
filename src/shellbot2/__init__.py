import asyncio
import os
import sys


def main() -> None:
    """Run the ShellBot2 command-line interface.

    A macOS app bundle has no command-line arguments when double-clicked, so
    its default behavior is to start the persistent daemon. Regular command
    line invocations retain their explicitly supplied arguments.
    """

    is_frozen_app = getattr(sys, "frozen", False)
    if is_frozen_app:
        # Logfire's Pydantic plugin calls inspect.getsource(), which is not
        # available from PyInstaller's frozen modules and aborts startup.
        os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "logfire-plugin")

    is_bundle_launch = len(sys.argv) == 1 or all(
        argument.startswith("-psn_") for argument in sys.argv[1:]
    )
    if is_frozen_app and is_bundle_launch:
        sys.argv = [sys.argv[0], "daemon", "start"]

    if is_frozen_app and sys.platform == "darwin":
        from shellbot2.macos_app import run_daemon_application

        from shellbot2.cli import main as cli_main

        run_daemon_application(cli_main)
    else:
        from shellbot2.cli import main as cli_main

        asyncio.run(cli_main())


if __name__ == "__main__":
    main()
