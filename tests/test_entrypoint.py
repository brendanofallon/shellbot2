import os
import sys
from unittest.mock import AsyncMock, patch

import shellbot2


def test_main_runs_cli_with_explicit_command(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["shellbot2", "daemon", "stop"])

    with patch("shellbot2.cli.main", new_callable=AsyncMock) as cli_main:
        shellbot2.main()

    cli_main.assert_awaited_once_with()
    assert sys.argv == ["shellbot2", "daemon", "stop"]


def test_frozen_app_defaults_to_starting_daemon(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ShellBot2"])
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("PYDANTIC_DISABLE_PLUGINS", raising=False)

    with (
        patch("shellbot2.cli.main", new_callable=AsyncMock) as cli_main,
        patch("shellbot2.macos_app.run_daemon_application") as run_macos_app,
    ):
        shellbot2.main()

    run_macos_app.assert_called_once_with(cli_main)
    assert sys.argv == ["ShellBot2", "daemon", "start"]
    assert os.environ["PYDANTIC_DISABLE_PLUGINS"] == "logfire-plugin"


def test_frozen_app_ignores_macos_process_serial_number(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ShellBot2", "-psn_0_12345"])
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")

    with (
        patch("shellbot2.cli.main", new_callable=AsyncMock) as cli_main,
        patch("shellbot2.macos_app.run_daemon_application") as run_macos_app,
    ):
        shellbot2.main()

    run_macos_app.assert_called_once_with(cli_main)
    assert sys.argv == ["ShellBot2", "daemon", "start"]
