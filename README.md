# ShellBot2

An AI agent daemon that uses ZeroMQ for input/output communication.

## Architecture

The AgentDaemon provides a persistent service that:
- **Listens** for JSON-formatted InputMessages on a ZeroMQ PULL socket (input)
- **Publishes** AG-UI events as JSON on a ZeroMQ PUSH socket (output)
- Only writes log messages to stdout (all agent output goes through ZeroMQ)

### ZeroMQ Socket Pattern

```
Client (PUSH) → Daemon Input (PULL)
                    ↓
                Agent Processing
                    ↓
Daemon Output (PUSH) → Client (PULL)
```

## CLI Usage

ShellBot2 provides two modes of operation: **daemon mode** for persistent background service, and **direct mode** for one-off queries.

### Direct Ask Mode

Direct ask mode runs a single prompt through the agent without starting a daemon. This is useful for quick, one-off queries.

```bash
# Run a prompt directly
python -m shellbot2.cli ask "What is the current directory?"

# Start a new conversation thread
python -m shellbot2.cli ask --new-thread "Hello, start a fresh conversation"
```

**Features:**
- Uses Rich formatting for beautiful terminal output
- No daemon required
- Terminates after completing the prompt
- Can start a new thread with `--new-thread` flag
- Maintains conversation history across runs (unless `--new-thread` is specified)

**Data Directory:**
All data (message history, logs, configuration) is stored in `~/.shellbot2` by default, or you can specify a custom location:

```bash
python -m shellbot2.cli --datadir /path/to/data ask "Your prompt"
```

### Daemon Mode

Daemon mode runs ShellBot2 as a persistent background service that listens for prompts on a ZeroMQ socket. This is ideal for integrating with other applications or running long-lived agent tasks.

#### Starting the Daemon

```bash
# Start the daemon (reads ZeroMQ addresses from agent_conf.yaml)
python -m shellbot2.cli daemon start

# Or specify custom data directory
python -m shellbot2.cli --datadir /path/to/data daemon start
```

The daemon will:
- Run in the foreground and log to both stdout and `shellbot2.log`
- Create a PID file at `~/.shellbot2/daemon.pid`
- Listen for prompts on the configured input address
- Publish events to the configured output address
- Maintain conversation history across prompts

#### Stopping the Daemon

```bash
# Stop the running daemon
python -m shellbot2.cli daemon stop
```

This sends a SIGTERM signal to the daemon process and cleans up the PID file.

#### Daemon Ask

Send a prompt to a running daemon and display the streaming results in your terminal:

```bash
# Send a prompt to the daemon
python -m shellbot2.cli daemon ask "What files are in the current directory?"
```

**How it works:**
- Connects to both the input and output ZeroMQ sockets
- Sends your prompt to the daemon via the input socket
- Subscribes to the output socket to receive streaming events
- Displays the agent's response in real-time with Rich formatting
- Automatically terminates when the agent completes

**Note:** While a `daemon ask` session is active, it creates a presence file that signals `daemon watch` to suppress its display, preventing duplicate output.

#### Daemon Watch

Watch mode is a persistent listener that displays daemon output when no `daemon ask` session is active. This is useful for monitoring background tasks, subtask alerts, or other events that occur when you're not actively querying the daemon.

```bash
# Start watching daemon output
python -m shellbot2.cli daemon watch
```

**Features:**
- Runs continuously until interrupted (Ctrl+C)
- Displays all agent output and events
- Automatically suppresses its display when a `daemon ask` session is active
- Resets its state after each `daemon ask` session to avoid displaying stale data
- Ideal for leaving open in a terminal to monitor background activity

**Use cases:**
- Monitoring subtask alerts and notifications
- Watching background agent activities
- Debugging daemon behavior
- Keeping track of scheduled or automated tasks

### Sending Messages to the Daemon

#### Using the CLI

The easiest way is to use `daemon ask` as shown above. For programmatic access, see below.

#### Using Python with ZeroMQ

```python
import zmq
import json
from datetime import datetime

context = zmq.Context()
socket = context.socket(zmq.PUSH)
socket.connect("tcp://127.0.0.1:5555")  # Use your configured input_address

message = {
    "prompt": "What is the current directory?",
    "source": "my_client",
    "datetime": datetime.now().isoformat(),
}

socket.send_json(message)
socket.close()
context.term()
```

### Receiving Events from the Daemon

The daemon publishes AG-UI events as JSON. Connect a SUB socket to receive them:

```python
import zmq
import json

context = zmq.Context()
socket = context.socket(zmq.SUB)
socket.setsockopt(zmq.SUBSCRIBE, b"")  # Subscribe to all messages
socket.connect("tcp://127.0.0.1:5556")  # Use your configured output_address

while True:
    event_json = socket.recv_string()
    event = json.loads(event_json)
    
    # Process event based on type
    event_type = event.get('type')
    if event_type == 'TEXT_MESSAGE_CONTENT':
        print(event.get('delta', ''), end='', flush=True)
    elif event_type in ('RUN_FINISHED', 'RUN_ERROR'):
        break
```

See `examples/zmq_client.py` for a complete example.

## Event Types

The daemon streams these AG-UI event types:
- `RUN_START`: Agent run begins
- `TEXT_MESSAGE_START`: Text message begins
- `TEXT_MESSAGE_CONTENT`: Text content delta
- `TEXT_MESSAGE_END`: Text message completes
- `TOOL_CALL_START`: Tool invocation begins
- `TOOL_CALL_ARGS`: Tool arguments (streaming)
- `TOOL_CALL_END`: Tool call completes
- `TOOL_CALL_RESULT`: Tool execution result
- `RUN_FINISHED`: Agent run completes successfully
- `RUN_ERROR`: Agent run encountered an error

Each event is a JSON object with a `type` field and type-specific fields.

## Configuration: agent_conf.yaml

ShellBot2 requires an `agent_conf.yaml` file in the data directory (default: `~/.shellbot2/agent_conf.yaml`). This file configures the agent's behavior, model selection, tool availability, and system instructions.

### Configuration Structure

```yaml
# Model Configuration
# Provider options: gemini, claude, openai, openrouter
provider: gemini
model: gemini-3-flash-preview

# ZeroMQ Socket Addresses (for daemon mode)
input_address: tcp://127.0.0.1:8527
output_address: tcp://127.0.0.1:8528

# Message History Settings
# Number of recent messages to include in context
recent_messages_limit: 10

# Optional context compaction settings
# Older/longer assistant messages are progressively truncated as burden grows
context_compaction:
    burden_threshold: 80000
    base_weight: 1.0
    weight_growth: 0.35
    interior_min_length: 700
    final_min_length: 3500
    preserve_head_chars: 240
    preserve_tail_chars: 240
    truncation_marker: "\n\n... message truncated ...\n\n"
    max_total_length: 60000

# Tool Configuration
# List of tools available to the agent
tools:
    - shell              # Execute shell commands
    - python             # Execute Python code
    - tavilysearch       # Web search via Tavily API
    - reader             # Read web pages and documents
    - clipboard          # Access system clipboard
    - fastmail           # Email integration (requires credentials)
    - calendar           # Calendar integration (requires credentials)
    - image-generator    # Generate images
    - memory             # Store and retrieve persistent memories
    - document-store:    # Document storage with semantic search
        store_id: your-store-id-here

# System Instructions
# Define the agent's personality, capabilities, and behavior
instructions: >
    You are a helpful AI assistant. You can execute shell commands,
    run Python code, search the web, and more. Always explain your
    reasoning and provide detailed responses.
```

### Configuration Fields

#### Model Configuration

- **`provider`** (required): The LLM provider to use
  - Options: `gemini`, `claude`, `openai`, `openrouter`
  - Example: `provider: gemini`

- **`model`** (required): The specific model to use
  - For Gemini: `gemini-3-flash-preview`, `gemini-2.0-flash`, etc.
  - For Claude: `claude-3-5-sonnet-20241022`, `claude-haiku-4.5`, etc.
  - For OpenAI: `gpt-4-turbo`, `gpt-4o`, etc.
  - For OpenRouter: Use format `provider/model`, e.g., `anthropic/claude-haiku-4.5`

#### ZeroMQ Configuration (Daemon Mode)

- **`input_address`** (required for daemon): ZeroMQ address for receiving prompts
  - Format: `tcp://host:port`
  - Example: `tcp://127.0.0.1:5555`
  - The daemon binds to this address; clients connect to it

- **`output_address`** (required for daemon): ZeroMQ address for publishing events
  - Format: `tcp://host:port`
  - Example: `tcp://127.0.0.1:5556`
  - The daemon binds to this address; clients connect to it

#### Message History

- **`recent_messages_limit`** (optional, default: 5): Number of recent messages to include in the context
  - Higher values provide more context but increase token usage
  - Example: `recent_messages_limit: 10`

- **`context_compaction`** (optional): Truncates long assistant/tool messages in a copy of recent context before sending to the model
  - Preserves message JSON structure and keeps user messages unmodified
  - Processes interactions from newest to oldest with a burden model
  - Final assistant result in an interaction is only truncated when very long
  - Configurable fields:
    - `burden_threshold` (default `80000`)
    - `base_weight` (default `1.0`)
    - `weight_growth` (default `0.35`)
    - `interior_min_length` (default `700`)
    - `final_min_length` (default `3500`)
    - `preserve_head_chars` / `preserve_tail_chars` (default `240`)
    - `truncation_marker` (default `"\n\n... message truncated ...\n\n"`)
    - `max_total_length` (default `60000`)

#### Tools

- **`tools`** (optional): List of tools available to the agent. If omitted, all built-in tools are loaded by default.
  - Each tool name corresponds to a tool implementation
  - Some tools (like `document-store`) support additional configuration
  - **Dynamic Plugin Support**: You can easily add custom tools by creating a `tools/` directory inside your `~/.shellbot2` datadir (e.g. `~/.shellbot2/tools/`). Any `.py` file placed there containing a class with a `toolname` property and a `__call__` method will be automatically discovered and can be enabled by adding its `toolname` to the `tools:` list!
  - Available built-in tools:
    - `shell`: Execute shell commands
    - `python`: Execute Python code
    - `tavilysearch`: Web search (requires Tavily API key)
    - `reader`: Read web pages and documents
    - `clipboard`: Access system clipboard
    - `fastmail`: Email integration (requires Fastmail credentials)
    - `calendar`: Calendar integration (requires Google Calendar credentials)
    - `image-generator`: Generate images
    - `memory`: Store and retrieve persistent information
    - `document-store`: Semantic search over documents (requires `store_id`)
    - `conversation-search`: Search past conversation history
    - `subtasks`: Run async python modules in the background
    - `file_search`: Search files using regex
    - `text_replace`: Replace exact text occurrences in a single file

Example with tool configuration:
```yaml
tools:
    - shell
    - python
    - my-custom-tool   # Discovered from ~/.shellbot2/tools/my_custom_tool.py
    - document-store:
        store_id: 903cb699-de81-4507-9e9a-17befc2c6ac8
```

#### System Instructions

- **`instructions`** (required): Multi-line string defining the agent's behavior
  - Sets the agent's personality and communication style
  - Defines capabilities and available tools
  - Provides guidelines for task execution
  - Can include specific domain knowledge or preferences

Example:
```yaml
instructions: >
    You are a helpful AI assistant with access to shell commands and Python.
    Always explain your reasoning step-by-step. When asked to perform tasks,
    break them down into smaller steps and verify your results.
```

### Example Configuration

Here's a complete example configuration:

```yaml
provider: gemini
model: gemini-3-flash-preview

input_address: tcp://127.0.0.1:8527
output_address: tcp://127.0.0.1:8528

recent_messages_limit: 10
context_compaction:
    burden_threshold: 80000
    base_weight: 1.0
    weight_growth: 0.35
    interior_min_length: 700
    final_min_length: 3500
    preserve_head_chars: 240
    preserve_tail_chars: 240
    truncation_marker: "\n\n... message truncated ...\n\n"
    max_total_length: 60000

tools:
    - shell
    - python
    - tavilysearch
    - reader
    - clipboard
    - memory

instructions: >
    You are an intelligent and helpful AI assistant. You have access to
    shell commands for system operations, Python for data processing,
    web search for current information, and tools for reading documents.
    
    Always provide detailed, technical answers. Break complex problems
    into smaller steps. Verify your results before responding.
    
    When executing commands, explain what you're doing and why. If something
    fails, analyze the error and propose solutions.
```

### Required External Credentials

Some tools require external credentials:

- **Fastmail**: Requires Fastmail API credentials
- **Calendar**: Requires Google Calendar API credentials
- **Tavily Search**: Requires Tavily API key
- **Document Store**: Requires a configured document store ID

Credentials are typically stored separately from `agent_conf.yaml` for security.
