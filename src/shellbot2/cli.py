import argparse
import asyncio
import logging
import os
import signal
import sys
import uuid
from pathlib import Path
from datetime import datetime
import time
import zmq
from dotenv import load_dotenv

from ag_ui.core import BaseEvent
from shellbot2.agent import ShellBot3, load_conf
from shellbot2.event_dispatcher import create_rich_output_dispatcher, RichOutputHandler
from shellbot2.memory_extractor import MemoryExtractor
from shellbot2.message_history import MessageHistory
from shellbot2.tools.memorytool import MemoryTool

load_dotenv()

logger = logging.getLogger(__name__)

def setup_logging(datadir: Path, stream_to_stdout: bool = False) -> None:
    """Configure logging to write to shellbot3.log in the datadir.
    
    Args:
        datadir: Path to the data directory where logs will be stored.
        stream_to_stdout: If True, also stream log messages to stdout.
    """
    log_file = datadir / "shellbot2.log"
    handlers: list[logging.Handler] = [logging.FileHandler(log_file)]

    if stream_to_stdout:
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setLevel(logging.INFO)
        handlers.append(stdout_handler)
    
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] [PID %(process)d]  %(name)s   %(levelname)s  %(message)s',
        handlers=handlers,
    )
    logging.info(f"Logging initialized, writing to {log_file}")


def get_pid_file(datadir: Path) -> Path:
    """Get the path to the daemon PID file."""
    return datadir / "daemon.pid"


def get_ask_presence_file(datadir: Path) -> Path:
    """Get the path to the daemon_ask presence file.
    
    This file exists while a daemon_ask session is actively reading from the
    output socket. The daemon_watch background listener checks for this file
    and suppresses its own display when it exists, so that daemon_ask has
    exclusive control of the output.
    """
    return datadir / "daemon_ask.presence"


async def run_prompt(args: argparse.Namespace) -> None:
    """Run a prompt directly through the agent."""
    logger = logging.getLogger(__name__)
    logger.info(f"Running prompt: {args.prompt[:100]}...")
    
    thread_id = None
    if args.new_thread:
        thread_id = str(uuid.uuid4())
        logger.info(f"Starting new thread with ID: {thread_id}")
    
    event_dispatcher = create_rich_output_dispatcher()
    agent = ShellBot3(args.datadir, thread_id=thread_id, event_dispatcher=event_dispatcher)
    
    try:
        await agent.run(args.prompt)
        logger.info("Prompt completed successfully")
    except Exception as e:
        logger.error(f"Error running prompt: {e}", exc_info=True)
        raise


async def daemon_start(args: argparse.Namespace) -> None:
    """Start the agent daemon."""
    from shellbot2.daemon import AgentDaemon
    
    logger = logging.getLogger(__name__)
    pid_file = get_pid_file(args.datadir)
    
    # Check if daemon is already running
    if pid_file.exists():
        pid = int(pid_file.read_text().strip())
        try:
            os.kill(pid, 0)  # Check if process exists
            logger.warning(f"Daemon already running with PID {pid}")
            print(f"Daemon already running with PID {pid}")
            sys.exit(1)
        except OSError:
            # Process doesn't exist, remove stale PID file
            logger.info(f"Removing stale PID file for PID {pid}")
            pid_file.unlink()
    
    # Write PID file
    args.datadir.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    pid_file.write_text(str(pid))
    
    # Load config to get addresses for logging
    conf = load_conf(args.datadir)
    input_address = conf.get('input_address', 'tcp://127.0.0.1:5555')
    output_address = conf.get('output_address', 'tcp://127.0.0.1:5556')
    logger.info(f"Starting daemon with PID {pid}, input_address={input_address}, output_address={output_address}")
    
    daemon = AgentDaemon(datadir=args.datadir)
    
    try:
        await daemon.start()
    except KeyboardInterrupt:
        logger.info("Daemon interrupted by user")
    finally:
        await daemon.stop()
        if pid_file.exists():
            pid_file.unlink()
            logger.info("PID file removed")


def daemon_stop(args: argparse.Namespace) -> None:
    """Stop a running daemon."""
    logger = logging.getLogger(__name__)
    pid_file = get_pid_file(args.datadir)
    
    if not pid_file.exists():
        logger.warning("No daemon PID file found")
        print("No daemon PID file found. Daemon may not be running.")
        sys.exit(1)
    
    pid = int(pid_file.read_text().strip())
    logger.info(f"Attempting to stop daemon with PID {pid}")
    
    try:
        os.kill(pid, signal.SIGTERM)
        logger.info(f"Sent SIGTERM to daemon (PID {pid})")
        print(f"Sent stop signal to daemon (PID {pid})")
    except OSError as e:
        logger.error(f"Failed to stop daemon: {e}")
        print(f"Failed to stop daemon: {e}")
        # Clean up stale PID file
        pid_file.unlink()
        sys.exit(1)


def daemon_is_running(datadir: Path) -> bool:
    """Check if the daemon is running."""
    pid_file = get_pid_file(datadir)
    if not pid_file.exists():
        return False
    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


async def daemon_ask(args: argparse.Namespace) -> None:
    """Send a prompt to the running daemon and display streaming results."""
    
    if not daemon_is_running(args.datadir):
        logger.error("Daemon is not running")
        print("Error: Daemon is not running. Start it with 'cli daemon start'")
        sys.exit(1)
    
    # Load config to get addresses
    conf = load_conf(args.datadir)
    input_address = conf.get('input_address', 'tcp://127.0.0.1:5555')
    output_address = conf.get('output_address', 'tcp://127.0.0.1:5556')
    
    logger.info(f"Sending prompt to daemon at {input_address}: {args.prompt[:100]}...")
    
    # Create the input message
    message = {
        "prompt": args.prompt,
        "source": "cli",
        "datetime": datetime.now().isoformat(),
    }
    
    context = zmq.Context()
    
    # Connect to output socket FIRST (as SUB) so the subscription is
    # established before the daemon begins responding to our prompt.
    output_socket = context.socket(zmq.SUB)
    output_socket.setsockopt(zmq.SUBSCRIBE, b"")
    output_socket.connect(output_address)
    
    # Small delay to allow subscription to establish (ZeroMQ "slow joiner" issue)
    time.sleep(0.1)
    
    # Write presence file so daemon_watch knows to suppress its display
    presence_file = get_ask_presence_file(args.datadir)
    presence_file.write_text(str(os.getpid()))
    
    # Now send the prompt via the input socket
    input_socket = context.socket(zmq.PUSH)
    input_socket.setsockopt(zmq.LINGER, 1000)  # Wait up to 1 second on close
    input_socket.connect(input_address)
    
    # Small delay to allow connection to establish
    time.sleep(0.1)
    
    input_socket.send_json(message)
    logger.info("Prompt sent successfully")
    input_socket.close()
    
    rich_handler = RichOutputHandler()
    
    # Receive and display events until run is complete
    try:
        while True:
            event_json = output_socket.recv_string()
            event = BaseEvent.model_validate_json(event_json)
            rich_handler.handle(event)
            
            event_type = getattr(event, 'type', None)
            if event_type:
                if hasattr(event_type, 'value'):
                    event_type = event_type.value
                
                # Stop when run finishes or errors
                if event_type in ('RUN_FINISHED', 'RUN_ERROR'):
                    break
    
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        rich_handler.cleanup()
    except Exception as e:
        logger.error(f"Error receiving events: {e}", exc_info=True)
        rich_handler.cleanup()
        raise
    finally:
        if presence_file.exists():
            presence_file.unlink()
        output_socket.close()
        context.term()


async def daemon_watch(args: argparse.Namespace) -> None:
    """Persistently watch for daemon output and display when daemon_ask is not active.
    
    This is a long-running process meant to be left open in a terminal. It
    subscribes to the daemon's output socket and displays events (e.g. subtask
    alert responses) that would otherwise go unseen because no daemon_ask
    session is running.
    
    When a daemon_ask session IS active (detected via a presence file), this
    watcher silently discards events to avoid duplicate display.
    """
    if not daemon_is_running(args.datadir):
        logger.error("Daemon is not running")
        print("Error: Daemon is not running. Start it with 'cli daemon start'")
        sys.exit(1)
    
    conf = load_conf(args.datadir)
    output_address = conf.get('output_address', 'tcp://127.0.0.1:5556')
    
    context = zmq.Context()
    output_socket = context.socket(zmq.SUB)
    output_socket.setsockopt(zmq.SUBSCRIBE, b"")
    output_socket.connect(output_address)
    
    logger.info(f"Watching daemon output at {output_address}")
    
    presence_file = get_ask_presence_file(args.datadir)
    rich_handler = RichOutputHandler()
    was_suppressed = False
    
    try:
        while True:
            event_json = output_socket.recv_string()
            
            ask_is_active = presence_file.exists()
            
            if ask_is_active:
                # daemon_ask is running and will handle display
                was_suppressed = True
                continue
            
            # If we were suppressing and daemon_ask just finished, reset the
            # handler so stale state (partial tool calls, live displays) from
            # the suppressed run doesn't leak into the next displayed run.
            if was_suppressed:
                rich_handler.cleanup()
                rich_handler = RichOutputHandler()
                was_suppressed = False
            
            event = BaseEvent.model_validate_json(event_json)
            rich_handler.handle(event)
    
    except KeyboardInterrupt:
        logger.info("Watch interrupted by user")
        rich_handler.cleanup()
        print("\nStopped watching.")
    finally:
        output_socket.close()
        context.term()


CHAT_HELP = """\
[bold cyan]ShellBot2 Interactive Chat[/bold cyan]
Type your message and press Enter to send. Available slash commands:
  [green]/new[/green]      Start a new conversation thread
  [green]/thread[/green]   Show the current thread ID
  [green]/threads[/green]  List all conversation thread IDs
  [green]/help[/green]     Show this help message
  [green]/quit[/green]     Exit chat  (also Ctrl-C or Ctrl-D)
"""

SLASH_COMMANDS = {"/new", "/thread", "/threads", "/help", "/quit", "/exit"}


def _get_input_gum(prompt_str: str) -> str | None:
    """Try to get input via `gum input`. Returns None if gum is unavailable or user cancels."""
    import shutil
    import subprocess
    if not shutil.which("gum"):
        return None
    try:
        result = subprocess.run(
            ["gum", "input", "--placeholder", "Type a message  (or /help for commands)…",
             "--prompt", prompt_str, "--width", "100"],
            capture_output=True, text=True
        )
        # gum exits with code 130 on Ctrl-C and 1 on Escape
        if result.returncode != 0:
            return ""   # treat as empty / cancelled
        return result.stdout.rstrip("\n")
    except Exception:
        return None


async def run_chat(args: argparse.Namespace) -> None:
    """Run an interactive multi-turn chat session directly (no daemon required).

    The session maintains a single conversation thread across turns. Slash
    commands give lightweight thread management without leaving the REPL:

        /new      — start a fresh thread
        /thread   — print the current thread ID
        /threads  — list all thread IDs in the history database
        /help     — show available commands
        /quit     — exit the session
    """
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.rule import Rule

    console = Console()
    console.print(Markdown(CHAT_HELP.replace("[bold cyan]", "# ").replace("[/bold cyan]", "")
                            .replace("[green]", "").replace("[/green]", "")))

    thread_id: str | None = None
    if getattr(args, "new_thread", False):
        thread_id = str(uuid.uuid4())
        logger.info(f"Chat starting new thread: {thread_id}")

    # We'll build the agent lazily (so we can re-create it on /new)
    event_dispatcher = create_rich_output_dispatcher(console)
    agent = ShellBot3(args.datadir, thread_id=thread_id, event_dispatcher=event_dispatcher)
    thread_id = agent.thread_id  # capture whatever thread was selected

    console.print(f"[dim]Thread: {thread_id}[/dim]")
    console.print(Rule(style="dim"))

    use_gum = True   # will be set to False if gum is not found on first attempt

    while True:
        prompt_str = "🤖 > "

        # --- Read input ---
        raw: str | None = None
        if use_gum:
            raw = _get_input_gum(prompt_str)
            if raw is None:
                # gum not available; fall back permanently
                use_gum = False

        if not use_gum:
            try:
                raw = input(prompt_str)
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Goodbye![/dim]")
                break

        if raw is None:
            # Shouldn't happen, but be safe
            break

        # gum cancelled with Ctrl-C / Escape returns empty string
        if raw == "" and use_gum:
            console.print("\n[dim]Goodbye![/dim]")
            break

        text = raw.strip()

        if not text:
            continue

        # --- Slash command handling ---
        if text.startswith("/"):
            cmd = text.split()[0].lower()

            if cmd in ("/quit", "/exit"):
                console.print("[dim]Goodbye![/dim]")
                break

            elif cmd == "/help":
                console.print(Markdown(CHAT_HELP.replace("[bold cyan]", "# ").replace("[/bold cyan]", "")
                                        .replace("[green]", "").replace("[/green]", "")))

            elif cmd == "/thread":
                console.print(f"[dim]Current thread: {agent.thread_id}[/dim]")

            elif cmd == "/threads":
                mh = MessageHistory(args.datadir / "message_history.db")
                ids = mh.get_thread_ids()
                if not ids:
                    console.print("[dim]No threads found.[/dim]")
                else:
                    console.print("[dim]All threads:[/dim]")
                    for tid in ids:
                        marker = " ← current" if tid == agent.thread_id else ""
                        console.print(f"  [dim]{tid}{marker}[/dim]")

            elif cmd == "/new":
                new_tid = str(uuid.uuid4())
                event_dispatcher = create_rich_output_dispatcher(console)
                agent = ShellBot3(args.datadir, thread_id=new_tid, event_dispatcher=event_dispatcher)
                thread_id = agent.thread_id
                console.print(Rule(style="dim"))
                console.print(f"[dim]New thread started: {thread_id}[/dim]")
                console.print(Rule(style="dim"))

            else:
                console.print(f"[yellow]Unknown command '{cmd}'. Type /help for available commands.[/yellow]")

            continue

        # --- Normal prompt ---
        console.print(Rule(style="dim"))
        try:
            await agent.run(text)
        except KeyboardInterrupt:
            console.print("\n[dim](interrupted)[/dim]")
        except Exception as e:
            logger.error(f"Error running prompt: {e}", exc_info=True)
            console.print(f"[red]Error: {e}[/red]")
        console.print(Rule(style="dim"))


async def extract_memories(args: argparse.Namespace) -> None:
    """Extract memories from conversation history and store them."""
    logger = logging.getLogger(__name__)

    conf = load_conf(args.datadir)
    message_history = MessageHistory(args.datadir / "message_history.db")
    memory_tool = MemoryTool()

    thread_id = getattr(args, "thread_id", None)
    if thread_id is None:
        thread_id = message_history.get_most_recent_thread_id()
        if thread_id is None:
            print("No conversation threads found in message history.")
            return

    limit = getattr(args, "limit", 10)

    extractor = MemoryExtractor(
        message_history=message_history,
        memory_tool=memory_tool,
        conf=conf,
    )

    logger.info(f"Extracting memories from thread {thread_id} (last {limit} interactions)")
    stored = await extractor.extract_and_store(thread_id, interaction_limit=limit)

    if not stored:
        print("No new memories extracted.")
        return

    print(f"\n  Extracted {len(stored)} memories:\n")
    for mem in stored:
        label = mem.category.upper()
        print(f"  [{label}]  {mem.key}")
        print(f"          {mem.value}\n")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        description='ShellBot3 CLI - Run prompts or manage the agent daemon'
    )
    parser.add_argument(
        '--datadir',
        type=Path,
        default=os.getenv('SHELLBOT_DATADIR', Path('~/.shellbot2').expanduser()),
        help='The directory to store data (default: ~/.shellbot2)'
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Direct prompt command (default behavior)
    prompt_parser = subparsers.add_parser(
        'ask',
        help='Run a prompt directly through the agent'
    )
    prompt_parser.add_argument(
        '--new-thread',
        action='store_true',
        help='Begin a new thread'
    )
    prompt_parser.add_argument(
        'prompt',
        type=str,
        help='The prompt to send to the agent'
    )
    
    # Daemon command group
    daemon_parser = subparsers.add_parser(
        'daemon',
        help='Daemon management commands'
    )
    daemon_subparsers = daemon_parser.add_subparsers(dest='daemon_command', help='Daemon commands')
    
    # daemon start
    daemon_start_parser = daemon_subparsers.add_parser(
        'start',
        help='Start the agent daemon (ZeroMQ addresses are read from agent_conf.yaml)'
    )
    
    # daemon stop
    daemon_subparsers.add_parser(
        'stop',
        help='Stop a running daemon'
    )
    
    # daemon ask
    daemon_ask_parser = daemon_subparsers.add_parser(
        'ask',
        help='Send a prompt to the running daemon (address is read from agent_conf.yaml)'
    )
    daemon_ask_parser.add_argument(
        'prompt',
        type=str,
        help='The prompt to send to the daemon'
    )
    
    # daemon watch
    daemon_subparsers.add_parser(
        'watch',
        help='Watch for daemon output (displays responses not handled by daemon ask)'
    )

    # Interactive chat command
    chat_parser = subparsers.add_parser(
        'chat',
        help='Start an interactive multi-turn chat session (no daemon required)'
    )
    chat_parser.add_argument(
        '--new-thread',
        action='store_true',
        help='Begin a new conversation thread instead of resuming the most recent one'
    )

    # Memory extraction command
    mem_parser = subparsers.add_parser(
        'extract-memories',
        help='Extract and store memories from recent conversation history'
    )
    mem_parser.add_argument(
        '--thread-id',
        type=str,
        default=None,
        help='Thread ID to extract from (default: most recent thread)'
    )
    mem_parser.add_argument(
        '--limit',
        type=int,
        default=10,
        help='Number of recent interactions to analyze (default: 10)'
    )

    return parser


async def main() -> None:
    parser = build_parser()
    
    args = parser.parse_args()
    
    # Ensure datadir exists and set up logging
    # Stream to stdout when running daemon in foreground
    args.datadir.mkdir(parents=True, exist_ok=True)
    stream_to_stdout = (args.command == 'daemon' and 
                        getattr(args, 'daemon_command', None) == 'start')
    setup_logging(args.datadir, stream_to_stdout=stream_to_stdout)
    
    logger = logging.getLogger(__name__)
    logger.info(f"CLI started with command: {args.command}")
    
    if args.command == 'ask':
        await run_prompt(args)
    elif args.command == 'chat':
        await run_chat(args)
    elif args.command == 'daemon':
        if args.daemon_command == 'start':
            await daemon_start(args)
        elif args.daemon_command == 'stop':
            daemon_stop(args)
        elif args.daemon_command == 'ask':
            await daemon_ask(args)
        elif args.daemon_command == 'watch':
            await daemon_watch(args)
        else:
            parser.parse_args(['daemon', '--help'])
    elif args.command == 'extract-memories':
        await extract_memories(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    asyncio.run(main())
