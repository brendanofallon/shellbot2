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

from ag_ui.core import BaseEvent
from shellbot2.agent import ShellBot3, load_conf
from shellbot2.event_dispatcher import create_rich_output_dispatcher, RichOutputHandler

    
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
    
    # Send the prompt via the input socket
    context = zmq.Context()
    input_socket = context.socket(zmq.PUSH)
    input_socket.setsockopt(zmq.LINGER, 1000)  # Wait up to 1 second on close
    input_socket.connect(input_address)
    
    # Small delay to allow connection to establish (ZeroMQ "slow joiner" issue)
    time.sleep(0.1)
    
    input_socket.send_json(message)
    logger.info("Prompt sent successfully")
    input_socket.close()
    
    # Connect to output socket to receive events
    output_socket = context.socket(zmq.PULL)
    output_socket.connect(output_address)
    
    # Small delay to allow connection to establish
    time.sleep(0.1)
    
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
        output_socket.close()
        context.term()


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
    
    return parser


async def main() -> None:
    parser = build_parser()
    
    args = parser.parse_args()
    
    # Ensure datadir exists and set up logging
    # Stream to stdout when running daemon in foreground
    args.datadir.mkdir(parents=True, exist_ok=True)
    stream_to_stdout = (args.command == 'daemon' and 
                        getattr(args, 'daemon_command', None) == 'start')
    setup_logging(args.datadir, stream_to_stdout=False)
    
    logger = logging.getLogger(__name__)
    logger.info(f"CLI started with command: {args.command}")
    
    if args.command == 'ask':
        await run_prompt(args)
    elif args.command == 'daemon':
        if args.daemon_command == 'start':
            await daemon_start(args)
        elif args.daemon_command == 'stop':
            daemon_stop(args)
        elif args.daemon_command == 'ask':
            await daemon_ask(args)
        else:
            parser.parse_args(['daemon', '--help'])
    else:
        parser.print_help()


if __name__ == '__main__':
    asyncio.run(main())
