"""
Agent daemon that listens for input messages via ZeroMQ and streams responses.

This module provides a persistent daemon that:
- Listens for JSON-formatted input messages on a ZeroMQ input socket
- Validates messages against the InputMessage schema
- Feeds prompts to the agent through a single serialized work queue
- Optionally runs opt-in sensors whose observations enqueue onto that queue
- Streams AG-UI events to a ZeroMQ output socket
- Writes log messages to both stdout and the log file

Human (ZeroMQ) messages and sensor events share one FIFO queue and one agent
worker, so only one ``agent.run()`` executes at a time.

Graceful shutdown waits up to ``GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS`` for the
in-flight agent run to finish after polling has been stopped.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import zmq
import zmq.asyncio

from shellbot2.agent import ShellBot3, load_conf
from shellbot2.database import database_path
from shellbot2.event_dispatcher import create_zeromq_dispatcher
from shellbot2.input_message import InputMessage
from shellbot2.sensorframework.config import SensorsConfig, parse_sensors_config, sensors_section_enabled
from shellbot2.sensorframework.discovery import discover_sensor_specs
from shellbot2.sensorframework.scheduler import SensorScheduler
from shellbot2.sensorframework.state_store import SqliteSensorStateStore


logger = logging.getLogger(__name__)

GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = 15
DEFAULT_QUEUE_MAXSIZE = 100

# Re-export for existing imports of InputMessage from this module.
__all__ = ["AgentDaemon", "InputMessage", "run_daemon", "GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS"]


class AgentDaemon:
    """
    Daemon that listens for input messages via ZeroMQ and runs the agent.

    The daemon binds to a ZeroMQ PULL socket for input messages and a ZeroMQ
    PUB socket for output events. Each external message should be JSON-formatted
    and conform to the InputMessage schema. Parsed messages are placed on an
    internal bounded FIFO queue; a single worker dequeues them and runs the
    agent so user and sensor turns cannot overlap.

    When sensors are disabled or absent from configuration, no sensor plugins
    are discovered or scheduled.
    """

    def __init__(self, datadir: Path):
        """Initialize the agent daemon.

        Args:
            datadir: Path to the data directory containing agent configuration.
        """
        self.datadir = Path(datadir)
        logger.info("Initializing AgentDaemon with datadir: %s", self.datadir)
        conf = load_conf(self.datadir)
        self.input_address = conf.get("input_address", "tcp://127.0.0.1:5555")
        self.output_address = conf.get("output_address", "tcp://127.0.0.1:5556")
        self.queue_maxsize = DEFAULT_QUEUE_MAXSIZE

        self._running = False
        self._stopped = False
        self._async_context: zmq.asyncio.Context | None = None
        self._input_socket: zmq.asyncio.Socket | None = None
        self._sync_context = None
        self._output_socket = None
        self._input_queue: asyncio.Queue[InputMessage] | None = None
        self._worker_task: asyncio.Task | None = None
        self._scheduler: SensorScheduler | None = None
        self._state_store: SqliteSensorStateStore | None = None
        self._sensors_config: SensorsConfig | None = None

        self._init_sensors(conf)

        # Create a synchronous output socket and bind it now, so the
        # ZeroMQEventHandler can send through it without creating its own.
        # A sync socket is required here because EventDispatcher.dispatch()
        # calls handle() synchronously (not awaited), and zmq.asyncio sockets
        # return coroutines from send_string() which would silently drop msgs.
        self._sync_context = zmq.Context()
        self._output_socket = self._sync_context.socket(zmq.PUB)
        self._output_socket.bind(self.output_address)

        self.dispatcher = create_zeromq_dispatcher(socket=self._output_socket)

        self.agent = ShellBot3(
            datadir=self.datadir,
            event_dispatcher=self.dispatcher,
        )
        self.logger = logging.getLogger(__name__)
        self.logger.info(
            "AgentDaemon initialized with datadir=%s, input_address=%s, output_address=%s",
            datadir,
            self.input_address,
            self.output_address,
        )

    def _init_sensors(self, conf: dict) -> None:
        if not sensors_section_enabled(conf):
            return

        specs = discover_sensor_specs(self.datadir / "sensors")
        sensors_config = parse_sensors_config(
            conf.get("sensors"),
            available_specs=specs,
        )
        self.queue_maxsize = sensors_config.queue_maxsize
        self._state_store = SqliteSensorStateStore(database_path(self.datadir))
        self._scheduler = SensorScheduler(
            sensors_config.entries,
            datadir=self.datadir,
            state_store=self._state_store,
            enqueue=self.enqueue_sensor_event,
            clock=self._clock,
        )
        self._sensors_config = sensors_config

    def _clock(self) -> datetime:
        return datetime.now(timezone.utc)

    def enqueue_sensor_event(self, message: InputMessage) -> bool:
        """Try to enqueue a sensor-originated message without blocking.

        Returns True if the message was queued. Returns False if the queue is
        not ready or full so the scheduler can leave the event undelivered.
        """

        if self._input_queue is None:
            self.logger.warning(
                "Dropping sensor event; input queue is not ready: sensor=%s event_id=%s",
                message.metadata.get("sensor_name"),
                message.event_id,
            )
            return False
        try:
            self._input_queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            self.logger.warning(
                "Dropping sensor event because the input queue is full: sensor=%s event_id=%s",
                message.metadata.get("sensor_name"),
                message.event_id,
            )
            return False

    async def start(self) -> None:
        """Start the daemon and begin listening for messages.

        Startup order: agent worker, then sensor scheduler, then the ZeroMQ
        receiver. This method runs until stop() is called or the process is
        interrupted.
        """
        self._async_context = zmq.asyncio.Context()

        self._input_socket = self._async_context.socket(zmq.PULL)
        self._input_socket.bind(self.input_address)

        await self._start_processing()

        self.logger.info("AgentDaemon started")
        self.logger.info("Input socket bound to %s", self.input_address)
        self.logger.info("Output socket bound to %s", self.output_address)
        logger.info(
            "AgentDaemon started - Input address: %s, Output address: %s",
            self.input_address,
            self.output_address,
        )

        try:
            await self._receive_loop()
        except asyncio.CancelledError:
            self.logger.info("Daemon cancelled")
            raise

    async def _start_processing(self) -> None:
        """Start the serialized agent worker, then the sensor scheduler."""

        self._running = True
        self._stopped = False
        if self._input_queue is None:
            self._input_queue = asyncio.Queue(maxsize=self.queue_maxsize)
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._agent_worker())
        if self._scheduler is not None:
            await self._scheduler.start()
            self._log_sensor_startup()

    def _log_sensor_startup(self) -> None:
        if self._sensors_config is None:
            return
        names = [entry.name for entry in self._sensors_config.entries]
        intervals = {entry.name: entry.interval_seconds for entry in self._sensors_config.entries}
        logger.info(
            "Sensors enabled: count=%s names=%s intervals=%s database=%s",
            len(names),
            names,
            intervals,
            database_path(self.datadir),
        )

    async def _receive_loop(self) -> None:
        assert self._input_socket is not None
        assert self._input_queue is not None
        while self._running:
            try:
                message_bytes = await self._input_socket.recv()
                message_str = message_bytes.decode("utf-8")
                logger.info("Received message: %s...", message_str[:100])
                try:
                    input_message = InputMessage.from_json(message_str)
                except (json.JSONDecodeError, ValueError) as e:
                    self.logger.error("Invalid message received: %s", e)
                    continue
                await self._input_queue.put(input_message)
            except zmq.ZMQError as e:
                if self._running:
                    self.logger.error("ZMQ error: %s", e)
            except asyncio.CancelledError:
                self.logger.info("Daemon cancelled")
                break

    async def _agent_worker(self) -> None:
        assert self._input_queue is not None
        while True:
            try:
                message = await asyncio.wait_for(self._input_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                if not self._running and self._input_queue.empty():
                    break
                continue
            except asyncio.CancelledError:
                break
            try:
                await self._process_input(message)
            finally:
                self._input_queue.task_done()
            if not self._running and self._input_queue.empty():
                break

    async def _process_input(self, input_message: InputMessage) -> None:
        if input_message.thread_id is not None:
            logger.info("Switching agent to thread: %s", input_message.thread_id)
            self.agent.thread_id = input_message.thread_id

        logger.info(
            "Processing message from %s: %s...",
            input_message.source,
            input_message.prompt[:100],
        )
        try:
            await self.agent.run(input_message.prompt)
            logger.info("Message processing completed successfully")
        except Exception as e:
            logger.error("Agent error: %s", e, exc_info=True)

    async def stop(self) -> None:
        """Stop polling, the agent worker, and ZeroMQ resources. Idempotent."""

        if self._stopped:
            self._close_sockets()
            return

        self.logger.info("Stopping AgentDaemon...")
        self._running = False
        self._stopped = True

        if self._scheduler is not None:
            await self._scheduler.stop()
            self._scheduler = None

        if self._state_store is not None:
            self._state_store.close()
            self._state_store = None

        if self._worker_task is not None:
            try:
                await asyncio.wait_for(
                    self._wait_for_worker(),
                    timeout=GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                self.logger.warning(
                    "Agent worker did not finish within %s seconds; cancelling",
                    GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
                )
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass
            self._worker_task = None

        self._close_sockets()
        self.logger.info("AgentDaemon stopped")
        print("AgentDaemon stopped")

    async def _wait_for_worker(self) -> None:
        if self._worker_task is None:
            return
        await self._worker_task

    def _close_sockets(self) -> None:
        if self._input_socket:
            self._input_socket.close()
            self._input_socket = None
        if self._output_socket:
            self._output_socket.close()
            self._output_socket = None
        if self._async_context:
            self._async_context.term()
            self._async_context = None
        if self._sync_context:
            self._sync_context.term()
            self._sync_context = None


async def run_daemon(datadir: Path) -> None:
    """Run the agent daemon.

    Convenience function to start and run the daemon until interrupted.

    Args:
        datadir: Path to the data directory containing agent configuration.
    """
    daemon = AgentDaemon(datadir=datadir)
    try:
        await daemon.start()
    except KeyboardInterrupt:
        pass
    finally:
        await daemon.stop()
