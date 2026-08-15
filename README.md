# ShellBot2

An AI agent harness that integrates easily into a shell and runs as a persistent service, using ZeroMQ for input/output communication. There is also a 'direct' mode for when you don't want to deal with the daemon. Daemon mode
enables the agent to respond to user messages and, when configured, to **sensor** events: opt-in scheduled plugins that
observe local or external state and enqueue a structured observation onto the daemon's serialized agent queue. 

The persistent daemon architecture with a message broker allows a lot of flexibility for how inputs are generated and 
where outputs go. Its easy enough to have a cli client that gives the user the ability to send a message, but input messages could easily come from sensors, web clients, other agents etc. Likewise, output messages (in the form of
[AGUI streams](https://docs.ag-ui.com/introduction)) can be rendered in many places - the terminal, a web client, etc

## Architecture

The AgentDaemon provides a persistent service that:
- **Listens** for JSON-formatted InputMessages on a ZeroMQ PULL socket (input)
- **Queues** user messages and optional sensor observations on one FIFO work queue so only one agent run executes at a time
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


## Sensors

Sensors are **opt-in scheduled plugins** that run inside the daemon. They periodically observe a data source and, when something notable happens, emit a structured observation. The framework turns that observation into a normal agent turn on the same FIFO queue used for ZeroMQ user messages, so only one `agent.run()` executes at a time.

A sensor is not a tool. It has no LLM, cannot call the agent or ZeroMQ, and does not get extra privileges. It returns facts; only framework-owned code may turn those facts into a prompt. That prompt is labeled as **untrusted external data**, so a raw email subject, web response, or other payload cannot act as an instruction.

If the `sensors` section is omitted from `agent_conf.yaml` or `enabled` is not `true`, the daemon does not discover or schedule any sensors.

### How the framework works

Two packages are involved:

- **`shellbot2.sensorframework`** owns discovery, YAML validation, the poll scheduler, namespaced SQLite state, deduplication/cooldowns, and prompt rendering.
- **`shellbot2.sensors`** holds packaged implementations (currently `disk_usage`). Custom plugins can also live as `*.py` files under `<datadir>/sensors/` (for example `~/.shellbot2/sensors/`).

At daemon startup, if sensors are enabled:

1. The framework discovers `SENSOR_SPECS` from packaged modules and from `<datadir>/sensors/*.py`. A custom spec with the same `name` as a packaged one replaces it. Duplicate names in the same source, import errors, and invalid specs are logged and skipped.
2. Configured entries are validated. An **enabled** entry that names a missing plugin fails startup. A **disabled** entry that names a missing plugin logs a warning and is ignored.
3. One asyncio task is started per enabled sensor. Each sensor is constructed once, polled immediately, then polled again only after `interval_seconds` has elapsed **since the previous poll finished** (polls never overlap).
4. Each `SensorObservation` is checked against durable delivery state keyed by `(sensor_name, dedupe_key)`. The same key is not enqueued again until `cooldown_seconds` has passed since the last **successful** enqueue.
5. A successful enqueue is converted with a framework-owned template into an `InputMessage` (`source` is `sensor:<name>`). User and sensor messages share one bounded FIFO queue and one agent worker.
6. If the queue is full, the sensor event is dropped and **not** marked delivered, so a later poll may retry. Delivery is at-most-once successful queue insertion per cooldown window: a crash after enqueue and before the agent run finishes can produce a later duplicate.

Plugin failures are fail-closed: a broken import, factory, or `poll()` is logged and retried on the next interval. It cannot stop other sensors or terminate the daemon.

State lives in SQLite (`sensor_state.db` under the data directory by default). Each plugin may `get` / `set` / `delete` JSON-safe values in its own namespace. Framework cooldown records use a reserved namespace a plugin cannot overwrite.

### Writing a sensor

A plugin is a single Python module. It does not subclass a framework base class. It must export `SENSOR_SPECS`, an iterable of `SensorSpec` objects:

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | YAML key and state namespace. A letter, then up to 63 letters, digits, `_`, or `-`. |
| `description` | yes | Human-readable description. |
| `factory` | yes | `factory(runtime) -> sensor`. Called once per daemon start (retried after the interval if it raises). |
| `default_interval_seconds` | no | Positive interval used when the YAML entry omits `interval_seconds` (library default 300). |

The object returned by `factory` needs one async method:

```python
async def poll(self, runtime: SensorRuntime) -> Sequence[SensorObservation]
```

Return an empty sequence when nothing notable happened. Do not call the agent, ZeroMQ, or the event dispatcher. Catch local I/O errors and return `[]` rather than raising, unless you want the scheduler to count a failure and retry later.

`SensorRuntime` is injected by the framework:

- `datadir` — the daemon data directory
- `sensor_name` — this plugin's configured name
- `config` — **this sensor's** `config:` mapping only, not the full `agent_conf.yaml`
- `state` — namespaced `get` / `set` / `delete` for JSON-serializable values
- `logger` — a logger for this sensor
- `now()` — injected clock (use this instead of `datetime.now()` so tests can freeze time)

Each observation is data, never a prompt:

| Field | Required | Meaning |
| --- | --- | --- |
| `kind` | yes | Short stable event type local to the sensor (single line). |
| `summary` | yes | Concise factual description for a human (size-bounded). |
| `dedupe_key` | yes | Stable id for this condition. Used only by the framework for cooldown; it is not an instruction to the model. |
| `payload` | no | JSON object of structured details (size-bounded). |
| `occurred_at` | no | `datetime`; defaults to the scheduler clock. |
| `severity` | no | `info`, `warning`, or `critical` (metadata only). |

Plugins cannot supply a ready-made prompt or system/developer message. The framework wraps `kind`, `severity`, time, `summary`, and JSON `payload` in a delimited untrusted-data block. Large payloads may be truncated in the prompt; the bounded payload is still stored on message metadata.

Minimal custom plugin (`~/.shellbot2/sensors/example_sensor.py`):

```python
from shellbot2.sensorframework import SensorObservation, SensorRuntime, SensorSpec


class ExampleSensor:
    async def poll(self, runtime: SensorRuntime):
        seen = runtime.state.get("seen")
        if seen:
            return []
        runtime.state.set("seen", True)
        return [
            SensorObservation(
                kind="first_run",
                summary="Example sensor completed its first poll.",
                dedupe_key="first-run",
                payload={"source": "example"},
            )
        ]


SENSOR_SPECS = (
    SensorSpec(
        name="example_sensor",
        description="Illustrative plugin; not bundled with ShellBot2.",
        factory=lambda runtime: ExampleSensor(),
        default_interval_seconds=300,
    ),
)
```

Enable it in `agent_conf.yaml` (see [Sensors configuration](#sensors-configuration) for every field):

```yaml
sensors:
  enabled: true
  entries:
    - name: example_sensor
      enabled: true
```

Leave `daemon watch` running to see the resulting agent turn. Sensor output uses the same ZeroMQ event stream as a user prompt.

### Bundled `disk_usage` sensor

`disk_usage` is an illustrative packaged plugin. It polls once per hour by default and emits a `low_disk_space` warning when free space on a path is **less than** `min_free_percent` (default 10). It uses `shutil.disk_usage` only; it does not run a shell.

```yaml
sensors:
  enabled: true
  entries:
    - name: disk_usage
      enabled: true
      cooldown_seconds: 0    # re-alert every poll while the condition holds
      config:
        path: /              # optional; defaults to the datadir's filesystem root
        min_free_percent: 10
```

There are no bundled mail, calendar, traffic, or hardware sensors.

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
    - desktop-notification # Native desktop notifications with optional replies
    - memory             # Store and retrieve persistent memories
    - document-store:    # Document storage with semantic search
        store_id: your-store-id-here

# System Instructions
# Define the agent's personality, capabilities, and behavior
instructions: >
    You are a helpful AI assistant. You can execute shell commands,
    run Python code, search the web, and more. Always explain your
    reasoning and provide detailed responses.

# Sensors (optional, opt-in)
# If this section is omitted or enabled is false, the daemon does not
# discover or schedule any sensors.
# sensors:
#   enabled: true
#   entries:
#     - name: disk_usage
#       enabled: true
#       config:
#         path: /
#         min_free_percent: 10
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

- **`tools`** (optional): List of tools available to the agent. If omitted, all discovered tools are loaded by default.
  - Each tool name corresponds to a tool implementation
  - Some tools (like `document-store`) support additional configuration
  - **Dynamic Plugin Support**: Shellbot scans both its packaged `shellbot2.tools` directory and the `tools/` directory inside the selected datadir (for example, `~/.shellbot2/tools/`). A custom tool with the same name as a packaged one intentionally overrides it; duplicate names within either directory are rejected.
  - Each loadable Python module exports `TOOL_SPECS`, an iterable of `ToolSpec` objects. A spec defines its required `name` (the YAML key), `description`, `parameters` (an object JSON Schema), and `factory` (which receives runtime dependencies plus configured keyword arguments and returns the callable implementation). `function_name` is optional and only needed when the model-facing function name differs from the YAML key.
  - Available built-in tools:
    - `shell`: Execute shell commands
    - `python`: Execute Python code
    - `tavilysearch`: Web search (requires Tavily API key)
    - `reader`: Read web pages and documents
    - `clipboard`: Access system clipboard
    - `fastmail`: Email integration (requires Fastmail credentials)
    - `calendar`: Calendar integration (requires Google Calendar credentials)
    - `image-generator`: Generate images
    - `image-reader`: Read local images for vision-capable models
    - `memory`: Store and retrieve persistent information
    - `document-store`: Semantic search over documents (requires `store_id`)
    - `conversation-search`: Search past conversation history
    - `subtasks`: Run async python modules in the background
    - `file-search`: Search files using regex
    - `text_replace`: Replace exact text occurrences in a single file
    - `notes`: Search and list personal notes
    - `desktop-notification`: Send native desktop notifications, optionally
      collecting a text reply from the user

`desktop-notification` exposes the model-facing function
`send_desktop_notification`. It requires a notification `title` and `message`;
`urgency`, `sound`, `thread`, and native display timeout are optional. Set
`reply_prompt` to include a text-reply field. The tool waits up to
`reply_timeout_seconds` (120 seconds by default), then returns a structured
result containing the notification ID, status (`replied`, `dismissed`,
`reply_timed_out`, or `reply_not_supported`), and the text response when one
was submitted. On macOS, the Python executable must be signed for native
notifications to be delivered.

Example with tool configuration:
```yaml
tools:
    - shell
    - python
    - my-custom-tool   # Discovered from ~/.shellbot2/tools/my_custom_tool.py
    - document-store:
        store_id: 903cb699-de81-4507-9e9a-17befc2c6ac8
```

Minimal custom plugin (`~/.shellbot2/tools/my_custom_tool.py`):
```python
from shellbot2.tools.tool_spec import ToolSpec


class MyCustomTool:
    def __init__(self, prefix="custom result"):
        self.prefix = prefix

    def __call__(self, **kwargs):
        return self.prefix


TOOL_SPECS = (
    ToolSpec(
        name="my-custom-tool",
        description="Returns a configurable custom result.",
        parameters={"type": "object", "properties": {}, "required": []},
        factory=lambda _runtime, kwargs: MyCustomTool(**kwargs),
    ),
)
```

#### Sensors configuration

See [Sensors](#sensors) for how the framework works and how to write a plugin. This section only documents `agent_conf.yaml` fields.

No sensor is loaded unless `sensors.enabled` is `true` and the sensor is listed under `entries` (entry `enabled` defaults to `true`). If the section is absent or `enabled` is not `true`, daemon behavior is unchanged.

```yaml
sensors:
  enabled: true
  state_db: sensor_state.db          # relative to datadir unless absolute
  default_interval_seconds: 300
  queue_maxsize: 100
  entries:
    - name: disk_usage
      enabled: true
      interval_seconds: 3600         # optional; otherwise the spec default
      cooldown_seconds: 0            # optional; default 0
      thread_id: sensor:disk_usage   # optional; default sensor:<name>
      config:                        # optional; passed only to this plugin
        path: /
        min_free_percent: 10
```

- **`enabled`** (optional, default treated as false): must be `true` to discover and schedule sensors.
- **`state_db`** (optional, default `sensor_state.db`): SQLite path, relative to the data directory unless absolute.
- **`default_interval_seconds`** (optional, default `300`): global default used when resolving intervals.
- **`queue_maxsize`** (optional, default `100`): bound on the daemon's shared user/sensor work queue.
- **`entries`**: list of mappings. Required per entry: **`name`**. Optional: **`enabled`**, **`interval_seconds`**, **`cooldown_seconds`**, **`thread_id`**, **`config`**.
- An enabled entry that names a missing plugin fails daemon startup. A disabled entry that names a missing plugin logs a warning and is ignored.

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
    max_total_length: 500000

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
