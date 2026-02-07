from shellbot2.subtask.subtaskrunner import SubTaskManager
from shellbot2.tools.util import classproperty
import json
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


_manager = None

class SubTaskTool:
    def __init__(self, subtask_modules_dir: Path):
        global _manager
        if _manager is None:
            _manager = SubTaskManager(subtask_modules_dir)
        self.manager = _manager

    @property
    def name(self):
        return "subtasks"
    
    @classproperty
    def toolname(cls):
        return "subtasks"
    
    @property
    def description(self):
        return """This function creates and manages subtasks. A subtask is a single python module that runs asynchronously in the background. 
        It can create new subtasks, list all subtasks with their name and status, retrieve the stdout and stderr of a subtask, and terminate subtasks. 
        To create a subtask, provide a unique name for the subtask and the full python module as a string. The module must provide a main() function, which will be executed when the subtask is started.
        The subtask will run asynchronously in the background, and the function will return immediately. The stdout and stderr from the subtask can be obtained at any time using the get_output operation.
        The subtask can be terminated using the terminate operation.
         """
    
    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["create", "list", "terminate", "get_output"],
                    "description": "The operation to perform. 'create' creates a new subtask, 'list' lists all subtasks with their status, 'terminate' terminates a running subtask, 'get_output' retrieves stdout, stderr, and error output from a subtask.",
                },
                "name": {
                    "type": "string",
                    "description": "The unique name of the subtask. Required for 'create', 'terminate', and 'get_output' operations.",
                },
                "code": {
                    "type": "string",
                    "description": "The full Python module code for the subtask. Must define a main() function which will be executed when the subtask starts. Required for the 'create' operation.",
                },
            },
            "required": ["operation"],
        }

    def __call__(self, **kwargs):
        operation = kwargs.get('operation')
        if operation == "create":
            name = kwargs.get('name')
            if name in self.manager:
                return f"Error: A subtask with name '{name}' already exists"
            code = kwargs.get('code')
            self.manager.create(name, code)
            return f"Successfully created subtask '{name}'"
        elif operation == "list":
            return json.dumps(self.manager.list())
        elif operation == "terminate":
            name = kwargs.get('name')
            if name not in self.manager:
                return f"Error: No subtask with name '{name}' found"
            self.manager.terminate(name)
        elif operation == "get_output":
            name = kwargs.get('name')
            stdout = self.manager.get(name).get_stdout()
            stderr = self.manager.get(name).get_stderr()
            error = self.manager.get(name).get_error()
            return f"Stdout: {stdout}\nStderr: {stderr}\nError: {error}"