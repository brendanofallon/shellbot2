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

## Usage

### Starting the Daemon

```bash
# Start with default addresses
python -m shellbot2.cli daemon start

# Or specify custom addresses
python -m shellbot2.cli daemon start \
  --input-address tcp://127.0.0.1:5555 \
  --output-address tcp://127.0.0.1:5556
```

### Sending Messages to the Daemon

The daemon expects JSON messages with this schema:
```json
{
  "prompt": "Your prompt here",
  "source": "client_name",
  "datetime": "2026-01-24T12:00:00.000000"
}
```

#### Using the CLI

```bash
python -m shellbot2.cli daemon ask "What is the current directory?"
```

#### Using Python with ZeroMQ

```python
import zmq
import json
from datetime import datetime

context = zmq.Context()
socket = context.socket(zmq.PUSH)
socket.connect("tcp://127.0.0.1:5555")

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

The daemon publishes AG-UI events as JSON. Connect a PULL socket to receive them:

```python
import zmq

context = zmq.Context()
socket = context.socket(zmq.PULL)
socket.connect("tcp://127.0.0.1:5556")

while True:
    event_json = socket.recv_string()
    event = json.loads(event_json)
    
    # Process event based on type
    event_type = event.get('type')
    if event_type == 'TEXT_MESSAGE_CONTENT':
        print(event.get('delta', ''), end='', flush=True)
    elif event_type == 'RUN_END':
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
- `RUN_END`: Agent run completes

Each event is a JSON object with a `type` field and type-specific fields.

## Daemon Management

```bash
# Start the daemon
python -m shellbot2.cli daemon start

# Stop the daemon
python -m shellbot2.cli daemon stop

# Send a prompt to running daemon
python -m shellbot2.cli daemon ask "Your prompt here"
```

## Direct Agent Usage (Non-Daemon)

You can also run prompts directly without the daemon:

```bash
python -m shellbot2.cli ask "Your prompt here"
```

This uses Rich formatting to display output directly in the terminal.
