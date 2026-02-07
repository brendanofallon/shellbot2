import subprocess
from pathlib import Path
import importlib.util
import sys
import time
import traceback
import multiprocessing as mp
import queue
from typing import Optional
import logging

# logging.basicConfig(level=logging.DEBUG, format='[%(asctime)s] [PID %(process)d]  %(name)s   %(levelname)s  %(message)s')
logger = logging.getLogger(__name__)


def load_module_from_file(module_path: Path):
    """
    Dynamically loads a Python module from a given file path.
    
    :param module_name: The name to assign to the module in sys.modules.
    :param file_path: The full path to the .py file.
    :return: The loaded module object.
    """
    logger.debug(f"Loading module from {module_path}")
    module_name = module_path.stem
    # Create a module specification from the file location
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    logger.debug(f"Spec: {spec}")
    # Create a new module based on the specification
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    logger.debug(f"Module: {module}")
    spec.loader.exec_module(module)

    return module

class _QueueWriter:
    """
    A file-like object that sends each write() call to a multiprocessing Queue immediately.
    """
    def __init__(self, q: mp.Queue):
        self._queue = q

    def write(self, s: str):
        if s:
            self._queue.put(s)

    def flush(self):
        pass


def _capture_output_wrapper(module_path, stdout_queue, stderr_queue, error_queue):
    """
    Wrapper function that loads a task module and redirects stdout and stderr
    to queues, streaming output as it is produced. If the task raises an
    exception, the formatted traceback is sent to error_queue.

    The module is loaded inside the child process to avoid pickling issues
    with the "spawn" start method (default on macOS).
    """
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = _QueueWriter(stdout_queue)
    sys.stderr = _QueueWriter(stderr_queue)
    
    try:
        task = load_module_from_file(module_path)
        task.main()
    except BaseException:
        tb = traceback.format_exc()
        error_queue.put(tb)
        # Also write to stderr so the traceback is captured in the stderr queue
        sys.stderr.write(tb)
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


class SubtaskHarness:
    """
    Manages execution of a subtask module in a separate process with output capture.
    """
    
    def __init__(self, module_path: Path):
        """
        Initialize the harness and start the subtask process.
        
        Args:
            module_path: Path to the Python module containing a main() function
        """
        self.module_path = module_path
        self._stdout_queue = mp.Queue()
        self._stderr_queue = mp.Queue()
        self._error_queue = mp.Queue()
        self._stdout_parts: list[str] = []
        self._stderr_parts: list[str] = []
        self._error_cache: Optional[str] = None
        self._start_time: Optional[float] = None
        self._end_time: Optional[float] = None
        
        self.process = mp.Process(
            target=_capture_output_wrapper,
            args=(module_path, self._stdout_queue, self._stderr_queue, self._error_queue)
        )
    
    def start(self):
        """
        Start the subtask process.
        """
        self._start_time = time.monotonic()
        self.process.start()
        logger.debug(f"Subtask {self.module_path.name} started with PID {self.process.pid}")
        return self.process

    def join(self, timeout: Optional[float] = None):
        """
        Wait for the subtask process to complete.
        
        Args:
            timeout: Optional timeout in seconds
        """
        self.process.join(timeout)
    
    def is_alive(self) -> bool:
        """Check if the subtask process is still running."""
        return self.process.is_alive()
    
    def _drain_queue(self, q: mp.Queue, parts: list[str]):
        """Drain all currently available items from a queue into a parts list."""
        while True:
            try:
                parts.append(q.get_nowait())
            except queue.Empty:
                break

    def get_stdout(self) -> str:
        """
        Get all stdout captured so far from the subtask.
        Can be called while the process is still running to get incremental output.
        """
        self._drain_queue(self._stdout_queue, self._stdout_parts)
        return "".join(self._stdout_parts)
    
    def get_stderr(self) -> str:
        """
        Get all stderr captured so far from the subtask.
        Can be called while the process is still running to get incremental output.
        """
        self._drain_queue(self._stderr_queue, self._stderr_parts)
        return "".join(self._stderr_parts)
    
    def get_error(self) -> Optional[str]:
        """
        Get the formatted traceback if the subtask raised an exception.

        Returns:
            The traceback string if an error occurred, None otherwise.
            Only available after the process has finished.
        """
        if self._error_cache is None and not self.is_alive():
            try:
                self._error_cache = self._error_queue.get_nowait()
            except queue.Empty:
                pass
        return self._error_cache

    @property
    def has_error(self) -> bool:
        """True if the subtask finished with an unhandled exception."""
        return self.get_error() is not None

    @property
    def exit_code(self) -> Optional[int]:
        """Get the exit code of the process, or None if still running."""
        return self.process.exitcode

    @property
    def elapsed(self) -> Optional[float]:
        """
        Seconds elapsed since start() was called.

        Returns:
            - None if the process has not been started yet.
            - Total runtime (fixed) if the process has finished.
            - Time since start (growing) if the process is still running.
        """
        if self._start_time is None:
            return None
        if self._end_time is not None:
            return self._end_time - self._start_time
        if not self.process.is_alive():
            self._end_time = time.monotonic()
            return self._end_time - self._start_time
        return time.monotonic() - self._start_time

    @property
    def status(self) -> str:
        """
        Return the current status of the subtask.
        
        Returns one of: "not started", "running", "error", "finished"
        """
        if self.process.pid is None:
            return "not started"
        elif self.process.is_alive():
            return "running"
        elif self.has_error:
            return "error"
        else:
            return "finished"

    def terminate(self):
        """Terminate the subtask process if it is running."""
        if self.process.is_alive():
            self.process.terminate()


class SubTaskManager:
    """
    Manages a collection of named subtask harnesses.
    """

    def __init__(self, modules_dir: Path):
        self._modules_dir = modules_dir
        if not self._modules_dir.exists():
            self._modules_dir.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, SubtaskHarness] = {}

    def create(self, name: str, code: str, start: bool = True) -> SubtaskHarness:
        """
        Create a new subtask with a unique name.

        Args:
            name: Unique name to identify this subtask.
            code: Code to be executed by the subtask.
            start: If True (default), start the subtask immediately.

        Returns:
            The created SubtaskHarness.

        Raises:
            ValueError: If a subtask with the given name already exists.
        """
        module_path = self._modules_dir / f"{name}.py"
        module_path.write_text(code)
        if name in self._tasks:
            raise ValueError(f"A subtask with name '{name}' already exists")
        harness = SubtaskHarness(module_path)
        self._tasks[name] = harness
        if start:
            harness.start()
        return harness

    def get(self, name: str) -> SubtaskHarness:
        """
        Get a subtask harness by name.

        Raises:
            KeyError: If no subtask with the given name exists.
        """
        if name not in self._tasks:
            raise KeyError(f"No subtask with name '{name}'")
        return self._tasks[name]

    def list(self) -> dict[str, dict]:
        """
        List all subtasks with their current status and elapsed time.

        Returns:
            Dict mapping subtask name to a dict with "status" and "elapsed_seconds" keys.
        """
        return {
            name: {
                "status": harness.status,
                "pid": harness.process.pid,
                "elapsed_seconds": f"{harness.elapsed:.4f}" if harness.elapsed is not None else "0",
            }
            for name, harness in self._tasks.items()
        }

    def terminate(self, name: str):
        """
        Terminate a subtask by name.

        Raises:
            KeyError: If no subtask with the given name exists.
        """
        harness = self.get(name)
        harness.terminate()

    def terminate_all(self):
        """Terminate all running subtasks."""
        for harness in self._tasks.values():
            harness.terminate()

    def remove(self, name: str):
        """
        Remove a subtask from the manager.
        Terminates it first if it is still running.

        Raises:
            KeyError: If no subtask with the given name exists.
        """
        harness = self.get(name)
        harness.terminate()
        harness.join(timeout=5)
        del self._tasks[name]

    def __len__(self) -> int:
        return len(self._tasks)

    def __contains__(self, name: str) -> bool:
        return name in self._tasks


def run_subtask(path_to_task_module: Path):
    """Run a subtask synchronously in the current process."""
    task = load_module_from_file(path_to_task_module)
    task.main()


TEST_CODE = """
import time
def main():
    for i in range(20):
        print(f"Subtask step {i}")
        time.sleep(0.25)
        if i == 10:
            raise Exception("Test error")
    print("Subtask finished")

"""

if __name__ == "__main__":
    manager = SubTaskManager(Path("~/src/shellbot2/test_subtaskdir").expanduser())
    manager.create("my_task", TEST_CODE, start=False)

    print("All tasks:", manager.list())

    manager.get("my_task").start()
    while manager.get("my_task").status == "running":
        print("stdout so far:", manager.get("my_task").get_stdout())
        print("stderr so far:", manager.get("my_task").get_stderr())
        print("error so far:", manager.get("my_task").get_error())
        time.sleep(0.52)
        print("All tasks:", manager.list())

    manager.get("my_task").terminate()
    print("All tasks:", manager.list())
    print("Final stdout:", manager.get("my_task").get_stdout())
    print("Final stderr:", manager.get("my_task").get_stderr())
    print("Final error:", manager.get("my_task").get_error())