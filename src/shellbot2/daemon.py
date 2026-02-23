"""
Agent daemon that listens for input messages via ZeroMQ and streams responses.

This module provides a persistent daemon that:
- Listens for JSON-formatted input messages on a ZeroMQ input socket
- Validates messages against the InputMessage schema
- Feeds prompts to the agent
- Streams AG-UI events to a ZeroMQ output socket
- Writes log messages to both stdout and the log file
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import logging

import zmq
import zmq.asyncio

from shellbot2.agent import ShellBot3, load_conf
from shellbot2.event_dispatcher import create_zeromq_dispatcher


logger = logging.getLogger(__name__)

@dataclass
class InputMessage:
    """Schema for incoming messages from ZeroMQ."""
    prompt: str
    source: str
    datetime: str
    thread_id: str | None = None
    
    @classmethod
    def from_json(cls, json_str: str) -> "InputMessage":
        """Parse an InputMessage from a JSON string.
        
        Args:
            json_str: JSON-encoded string with prompt, source, and datetime fields.
            
        Returns:
            An InputMessage instance.
            
        Raises:
            ValueError: If required fields are missing.
            json.JSONDecodeError: If the string is not valid JSON.
        """
        data = json.loads(json_str)
        required_fields = {"prompt", "source", "datetime"}
        missing = required_fields - set(data.keys())
        if missing:
            raise ValueError(f"Missing required fields: {missing}")
        return cls(
            prompt=data["prompt"],
            source=data["source"],
            datetime=data["datetime"],
            thread_id=data.get("thread_id"),
        )


class AgentDaemon:
    """
    Daemon that listens for input messages via ZeroMQ and runs the agent.
    
    The daemon binds to a ZeroMQ PULL socket for input messages and connects
    to a ZeroMQ PUSH socket for output events. Each message should be JSON-formatted
    and conform to the InputMessage schema. When a message is received, the prompt
    is fed to the agent and AG-UI events are streamed to the output socket.
    Daemon log messages are written to both stdout and the log file.
    """
    
    def __init__(
        self,
        datadir: Path,
    ):
        """Initialize the agent daemon.
        
        Args:
            datadir: Path to the data directory containing agent configuration.
        """
        self.datadir = Path(datadir)
        logger.info(f"Initializing AgentDaemon with datadir: {self.datadir}")
        # Load configuration
        conf = load_conf(self.datadir)
        self.input_address = conf.get('input_address', 'tcp://127.0.0.1:5555')
        self.output_address = conf.get('output_address', 'tcp://127.0.0.1:5556')
        
        self._running = False
        self._async_context: zmq.asyncio.Context | None = None
        self._input_socket: zmq.asyncio.Socket | None = None
        
        # Create a synchronous output socket and bind it now, so the
        # ZeroMQEventHandler can send through it without creating its own.
        # A sync socket is required here because EventDispatcher.dispatch()
        # calls handle() synchronously (not awaited), and zmq.asyncio sockets
        # return coroutines from send_string() which would silently drop msgs.
        self._sync_context = zmq.Context()
        self._output_socket = self._sync_context.socket(zmq.PUB)
        self._output_socket.bind(self.output_address)
        
        self.dispatcher = create_zeromq_dispatcher(socket=self._output_socket)
        
        # Create agent instance for this request
        self.agent = ShellBot3(
            datadir=self.datadir,
            event_dispatcher=self.dispatcher,
        )
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"AgentDaemon initialized with datadir={datadir}, input_address={self.input_address}, output_address={self.output_address}")
    
    async def start(self) -> None:
        """Start the daemon and begin listening for messages.
        
        This method runs indefinitely until stop() is called or the process
        is interrupted.
        """
        self._async_context = zmq.asyncio.Context()
        
        # Input socket: async PULL socket to receive InputMessages
        self._input_socket = self._async_context.socket(zmq.PULL)
        self._input_socket.bind(self.input_address)
        
        # Output socket is already bound in __init__ (sync socket)
        
        self._running = True
        
        self.logger.info(f"AgentDaemon started")
        self.logger.info(f"Input socket bound to {self.input_address}")
        self.logger.info(f"Output socket bound to {self.output_address}")
        logger.info(f"AgentDaemon started - Input address: {self.input_address}, Output address: {self.output_address}")
        
        while self._running:
            try:
                # Wait for incoming message
                message_bytes = await self._input_socket.recv()
                message_str = message_bytes.decode("utf-8")
                logger.info(f"Received message: {message_str[:100]}...")
                
                await self._handle_message(message_str)
                
            except zmq.ZMQError as e:
                if self._running:
                    self.logger.error(f"ZMQ error: {e}")
            except asyncio.CancelledError:
                self.logger.info("Daemon cancelled")
                break
    
    async def _handle_message(self, message_str: str) -> None:
        """Process an incoming message.
        
        Args:
            message_str: The raw message string received from ZeroMQ.
        """
        try:
            input_message = InputMessage.from_json(message_str)
        except (json.JSONDecodeError, ValueError) as e:
            self.logger.error(f"Invalid message received: {e}")
            return
        
        if input_message.thread_id is not None:
            logger.info(f"Switching agent to thread: {input_message.thread_id}")
            self.agent.thread_id = input_message.thread_id

        logger.info(f"Processing message from {input_message.source}: {input_message.prompt[:100]}...")
        
        try:
            await self.agent.run(input_message.prompt)
            logger.info("Message processing completed successfully")
        except Exception as e:
            logger.error(f"Agent error: {e}", exc_info=True)
    
    async def stop(self) -> None:
        """Stop the daemon and clean up resources."""
        self.logger.info("Stopping AgentDaemon...")
        self._running = False
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
        self.logger.info("AgentDaemon stopped")
        print("AgentDaemon stopped")


async def run_daemon(
    datadir: Path,
) -> None:
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
