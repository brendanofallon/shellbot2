import zmq
from datetime import datetime
import time
import logging

logger = logging.getLogger(__name__)



_conf = None

def set_conf(conf: dict):
    global _conf
    _conf = conf

def alert(message: str):
    """
    Alert the user with a message.
    """
    if _conf is None:
        raise RuntimeError(
            "Subtask helpers not initialized: set_conf() was never called. "
            "alert() can only be used inside a subtask managed by SubTaskManager."
        )
    input_address = _conf.get('zmq_input_address')
    if not input_address:
        raise RuntimeError(
            f"zmq_input_address is not set in subtask config. "
            f"Ensure 'input_address' is defined in agent_conf.yaml. Current config: {_conf}"
        )
    subtask_name = _conf.get('subtask_name')
    
    logger.info(f"Sending prompt to daemon at {input_address}: {message[:100]}...")
    
    message = f"Message from subtask {subtask_name}:\n {message}"
    
    message_payload = {
        "prompt": message,
        "source": f"subtask_{subtask_name}",
        "datetime": datetime.now().isoformat(),
    }
    
    # Send the prompt via the input socket
    context = zmq.Context()
    input_socket = context.socket(zmq.PUSH)
    input_socket.setsockopt(zmq.LINGER, 1000)  # Wait up to 1 second on close
    input_socket.connect(input_address)
    
    # Small delay to allow connection to establish (ZeroMQ "slow joiner" issue)
    time.sleep(0.2)
    
    input_socket.send_json(message_payload)
    logger.info("Prompt sent successfully")
    input_socket.close()