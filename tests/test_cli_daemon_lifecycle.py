import argparse
import asyncio
from unittest.mock import patch

from shellbot2.cli import daemon_start


class FakeDaemon:
    instances: list["FakeDaemon"] = []

    def __init__(self, datadir):
        self.datadir = datadir
        self.stop_calls = 0
        self.instances.append(self)

    async def start(self):
        raise KeyboardInterrupt

    async def stop(self):
        self.stop_calls += 1


def test_daemon_start_exposes_daemon_before_entering_receive_loop(tmp_path):
    FakeDaemon.instances.clear()
    ready_daemons = []
    args = argparse.Namespace(datadir=tmp_path)

    with (
        patch("shellbot2.cli.load_conf", return_value={}),
        patch("shellbot2.daemon.AgentDaemon", FakeDaemon),
    ):
        asyncio.run(daemon_start(args, on_daemon_ready=ready_daemons.append))

    daemon = FakeDaemon.instances[0]
    assert ready_daemons == [daemon]
    assert daemon.stop_calls == 1
    assert not (tmp_path / "daemon.pid").exists()
