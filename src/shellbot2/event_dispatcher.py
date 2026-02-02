"""
Event dispatcher for handling AG-UI events from the agent run stream.

This module provides an extensible event dispatching system that routes
events to registered handlers based on event type.
"""

from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Callable
import sys
import json

import zmq
from ag_ui.core import BaseEvent
from rich.box import Box
from rich.console import Console, Group
from rich.constrain import Constrain
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text


# Custom box with only left border (using half block for medium thickness)
LEFT_BORDER_BOX = Box(
    """\
▌   
▌   
    
▌   
    
    
▌   
▌   
"""
)


class EventHandler(ABC):
    """Abstract base class for event handlers."""
    
    @abstractmethod
    def handle(self, event: BaseEvent) -> None:
        """Handle an event.
        
        Args:
            event: The AG-UI event to handle.
        """
        raise NotImplementedError("Subclasses must implement this method")




class ZeroMQEventHandler(EventHandler):
    """Handler that publishes all events as JSON to a ZeroMQ socket.
    
    This handler serializes AG-UI events to JSON format and publishes them
    via a ZeroMQ PUSH socket. This allows external processes to receive
    and process events in real-time.
    
    The handler can either create its own socket or use an externally-provided
    socket (e.g., from a daemon that manages the connection).
    """
    
    def __init__(self, socket: zmq.Socket = None, connect_address: str = "tcp://127.0.0.1:5556"):
        """Initialize the ZeroMQ event handler.
        
        Args:
            socket: Optional pre-configured ZeroMQ socket to use. If provided,
                   connect_address is ignored and this handler will not manage
                   the socket lifecycle (no cleanup on close).
            connect_address: ZeroMQ address to connect to if socket is not provided
                           (default: tcp://127.0.0.1:5556).
        """
        self._owns_socket = socket is None
        
        if socket is not None:
            self._socket = socket
            self._context = None
        else:
            self.connect_address = connect_address
            self._context = zmq.Context()
            self._socket = self._context.socket(zmq.PUSH)
            self._socket.connect(connect_address)
    
    def handle(self, event: BaseEvent) -> None:
        """Serialize the event to JSON and send it via ZeroMQ.
        
        Args:
            event: The AG-UI event to publish.
        """
        # Use Pydantic's built-in JSON serialization
        event_json = event.model_dump_json()
        self._socket.send_string(event_json)
    
    def cleanup(self) -> None:
        """Clean up ZeroMQ resources if this handler owns them."""
        if self._owns_socket:
            if self._socket:
                self._socket.close()
            if self._context:
                self._context.term()


class RichOutputHandler(EventHandler):
    """
    Global handler that prints text and tool calls using Rich formatting.
    
    This handler tracks state across events to properly format:
    - Text messages: rendered as Markdown using Live display for streaming updates
    - Tool calls: displayed in colored panels with name, arguments, and results
    """
    
    MARKDOWN_WIDTH = 100
    MARKDOWN_LEFT_PADDING = 6
    
    def __init__(self, console: Console = None):
        """Initialize the Rich output handler.
        
        Args:
            console: Rich Console instance (creates one if not provided).
        """
        self.console = console or Console()
        self._tool_calls: dict[str, dict] = {}  # Track tool call state by ID
        self._current_message_id: str | None = None
        self._message_content: str = ""  # Accumulated markdown content
        self._live: Live | None = None  # Live display for streaming markdown
    
    def _render_markdown(self, content: str):
        """Render markdown content with width constraint and left padding."""
        md = Markdown(content)
        constrained = Constrain(md, width=self.MARKDOWN_WIDTH)
        return Padding(constrained, (0, 0, 0, self.MARKDOWN_LEFT_PADDING))
    
    def handle(self, event: BaseEvent) -> None:
        """Route event to appropriate handler method."""
        event_type = getattr(event, 'type', None)
        if event_type is None:
            return
        
        # Convert enum to string if necessary
        if hasattr(event_type, 'value'):
            event_type = event_type.value
        
        handler_method = getattr(self, f'_handle_{event_type.lower()}', None)
        if handler_method:
            handler_method(event)
    
    def _handle_text_message_start(self, event: BaseEvent) -> None:
        """Handle start of text message - initialize Live display."""
        self._current_message_id = getattr(event, 'message_id', None)
        self._message_content = ""
        # Start Live display for streaming markdown
        self._live = Live(
            self._render_markdown(""),
            console=self.console,
            refresh_per_second=10,
            vertical_overflow="visible",
        )
        self._live.start()
    
    def _handle_text_message_content(self, event: BaseEvent) -> None:
        """Handle text message content - accumulate and update Live markdown."""
        delta = getattr(event, 'delta', None)
        if delta:
            self._message_content += delta
            if self._live:
                self._live.update(self._render_markdown(self._message_content))
    
    def _handle_text_message_end(self, event: BaseEvent) -> None:
        """Handle end of text message - finalize Live display."""
        if self._live:
            # Final update with complete content
            self._live.update(self._render_markdown(self._message_content))
            self._live.stop()
            self._live = None
        self._current_message_id = None
        self._message_content = ""
        self.console.print()  # Add spacing after message
    
    def _handle_tool_call_start(self, event: BaseEvent) -> None:
        """Handle start of tool call - store state, don't print yet."""
        tool_call_id = getattr(event, 'tool_call_id', None)
        tool_call_name = getattr(event, 'tool_call_name', None)
        
        # If we're in a Live context, stop it temporarily for tool output
        if self._live:
            self._live.stop()
            self._live = None
        
        if tool_call_id:
            self._tool_calls[tool_call_id] = {
                'name': tool_call_name or 'unknown',
                'args': '',
                'result': None,
            }
    
    def _handle_tool_call_args(self, event: BaseEvent) -> None:
        """Handle tool call arguments - accumulate them."""
        tool_call_id = getattr(event, 'tool_call_id', None)
        delta = getattr(event, 'delta', None)
        
        if tool_call_id and tool_call_id in self._tool_calls and delta:
            self._tool_calls[tool_call_id]['args'] += delta
    
    def _handle_tool_call_end(self, event: BaseEvent) -> None:
        """Handle end of tool call - display tool name and arguments immediately."""
        tool_call_id = getattr(event, 'tool_call_id', None)
        
        if tool_call_id and tool_call_id in self._tool_calls:
            tool_info = self._tool_calls[tool_call_id]
            tool_name = tool_info.get('name', 'Tool')
            args_str = tool_info.get('args', '')
            
            # Build panel content
            panel_parts = []
            
            # Tool name header
            panel_parts.append(Text(f"🔧 {tool_name}", style="bold green"))
            
            # Format arguments
            if args_str:
                try:
                    args_obj = json.loads(args_str)
                    args_formatted = "\n".join(f"  {k}: {v}" for k, v in args_obj.items())
                    panel_parts.append(Text(args_formatted, style="bright blue"))
                except json.JSONDecodeError:
                    panel_parts.append(Text(f"  {args_str}", style="bright blue"))
            
            # Combine all parts
            combined_content = Group(*panel_parts)
            
            self.console.print()
            self.console.print(
                Padding(
                    Panel(
                        combined_content,
                        box=LEFT_BORDER_BOX,
                        border_style="bright_blue",
                        width=self.MARKDOWN_WIDTH,
                        style="on grey15",
                        padding=(0, 1, 0, 1), # top, right, bottom, left
                    ),
                    (0, 0, 0, 6), # top, right, bottom, left
                )
            )
    
    def _handle_tool_call_result(self, event: BaseEvent) -> None:
        """Handle tool call result - display result in panel below the tool call panel."""
        tool_call_id = getattr(event, 'tool_call_id', None)
        content = getattr(event, 'content', None)
        
        if content:
            # Truncate result to 250 chars
            display_content = content
            if len(content) > 250:
                display_content = content[:250] + "... (truncated)"
            
            result_text = Text(f"Result: {display_content}", style="dim")
            
            self.console.print(
                Padding(
                    Panel(
                    result_text,
                    box=LEFT_BORDER_BOX,
                    border_style="bright_blue",
                    width=self.MARKDOWN_WIDTH,
                    style="on grey15",
                    padding=(0, 1, 0, 1),
                    ),
                    (0, 0, 1, 6), # top, right, bottom, left
                )
        )
        
        # Clean up tool call tracking
        if tool_call_id and tool_call_id in self._tool_calls:
            del self._tool_calls[tool_call_id]
    
    def cleanup(self) -> None:
        """Clean up any active Live displays. Call this if processing is interrupted."""
        if self._live:
            self._live.stop()
            self._live = None


class EventDispatcher:
    """
    Dispatches AG-UI events to registered handlers based on event type.
    
    The dispatcher supports multiple handlers per event type and allows
    both class-based handlers (EventHandler subclasses) and simple callables.
    
    Example usage:
        dispatcher = EventDispatcher()
        
        # Register a class-based handler
        dispatcher.register("TEXT_MESSAGE_CONTENT", TextPrinterHandler())
        
        # Register a simple callable
        dispatcher.register("RUN_STARTED", lambda e: print(f"Run started: {e.run_id}"))
        
        # Dispatch events
        async for event in adapter.run_stream(...):
            dispatcher.dispatch(event)
    """
    
    def __init__(self):
        """Initialize the event dispatcher with empty handler registry."""
        self._handlers: dict[str, list[Callable[[BaseEvent], None]]] = defaultdict(list)
        self._global_handlers: list[Callable[[BaseEvent], None]] = []
    
    def register(self, event_type: str, handler: EventHandler | Callable[[BaseEvent], None]) -> "EventDispatcher":
        """Register a handler for a specific event type.
        
        Args:
            event_type: The event type to handle (e.g., "TEXT_MESSAGE_CONTENT").
            handler: Either an EventHandler instance or a callable that takes an event.
            
        Returns:
            Self, for method chaining.
        """
        if isinstance(handler, EventHandler):
            self._handlers[event_type].append(handler.handle)
        else:
            self._handlers[event_type].append(handler)
        return self
    
    def register_global(self, handler: EventHandler | Callable[[BaseEvent], None]) -> "EventDispatcher":
        """Register a handler that receives all events.
        
        Args:
            handler: Either an EventHandler instance or a callable that takes an event.
            
        Returns:
            Self, for method chaining.
        """
        if isinstance(handler, EventHandler):
            self._global_handlers.append(handler.handle)
        else:
            self._global_handlers.append(handler)
        return self
    
    def unregister(self, event_type: str, handler: EventHandler | Callable[[BaseEvent], None]) -> bool:
        """Unregister a handler for a specific event type.
        
        Args:
            event_type: The event type.
            handler: The handler to remove.
            
        Returns:
            True if the handler was found and removed, False otherwise.
        """
        handler_func = handler.handle if isinstance(handler, EventHandler) else handler
        if handler_func in self._handlers[event_type]:
            self._handlers[event_type].remove(handler_func)
            return True
        return False
    
    def dispatch(self, event: BaseEvent) -> None:
        """Dispatch an event to all registered handlers.
        
        Args:
            event: The AG-UI event to dispatch.
        """
        event_type = getattr(event, 'type', None)
        if event_type is None:
            return
        
        # Convert enum to string if necessary
        if hasattr(event_type, 'value'):
            event_type = event_type.value
        
        # Call type-specific handlers
        for handler in self._handlers.get(event_type, []):
            handler(event)
        
        # Call global handlers
        for handler in self._global_handlers:
            handler(event)
    
    def clear(self, event_type: str | None = None) -> None:
        """Clear registered handlers.
        
        Args:
            event_type: If provided, clear handlers for this type only.
                       If None, clear all handlers.
        """
        if event_type is None:
            self._handlers.clear()
            self._global_handlers.clear()
        else:
            self._handlers[event_type].clear()


def create_rich_output_dispatcher(console: Console = None) -> EventDispatcher:
    """Create a dispatcher with Rich formatting for text and tool calls.
    
    This creates a dispatcher with a global RichOutputHandler that:
    - Streams text message content to the console
    - Displays tool calls with colored panels showing name, arguments, and results
    
    Args:
        console: Optional Rich Console instance. Creates one if not provided.
        
    Returns:
        A configured EventDispatcher instance with Rich output.
    """
    dispatcher = EventDispatcher()
    dispatcher.register_global(RichOutputHandler(console))
    return dispatcher


def create_zeromq_dispatcher(socket: zmq.Socket = None, connect_address: str = "tcp://127.0.0.1:5556") -> EventDispatcher:
    """Create a dispatcher that publishes all events as JSON to a ZeroMQ socket.
    
    This creates a dispatcher with a global ZeroMQEventHandler that serializes
    all AG-UI events to JSON and publishes them via a ZeroMQ PUSH socket.
    
    Args:
        socket: Optional pre-configured ZeroMQ socket to use. If provided,
               connect_address is ignored.
        connect_address: ZeroMQ address to connect to if socket is not provided
                        (default: tcp://127.0.0.1:5556).
        
    Returns:
        A configured EventDispatcher instance with ZeroMQ publishing.
    """
    dispatcher = EventDispatcher()
    dispatcher.register_global(ZeroMQEventHandler(socket=socket, connect_address=connect_address))
    return dispatcher
