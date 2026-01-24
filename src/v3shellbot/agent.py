
from dataclasses import dataclass
import asyncio
from pathlib import Path
import yaml
import json
import uuid
from pprint import pprint

from pydantic_core import to_jsonable_python
from ag_ui.core import RunAgentInput, UserMessage
from pydantic_ai.ui.ag_ui import AGUIAdapter
from pydantic_ai import (
    Agent, 
    RunContext, 
    AgentRunResultEvent, 
    PartDeltaEvent, 
    TextPartDelta, 
    AgentRunResult,
    Tool, 
    ModelMessagesTypeAdapter
)
        
from v3shellbot.tools.botfunctions import ShellFunction, ReaderFunction, ClipboardFunction, PythonFunction, TavilySearchFunction

from v3shellbot.message_history import MessageHistory

def create_tool_from_schema(tool_cls):
    return Tool.from_schema(
        function=tool_cls.__call__,
        name=tool_cls.name,
        description=tool_cls.description,
        json_schema=tool_cls.parameters,
        takes_ctx=False,
    )

def create_tools():
    return [
        create_tool_from_schema(ShellFunction()),
        create_tool_from_schema(ReaderFunction()),
        create_tool_from_schema(ClipboardFunction()),
        create_tool_from_schema(PythonFunction()),
        create_tool_from_schema(TavilySearchFunction()),
    ]


def load_conf(datadir: Path):
    conf = {}
    conf_file = Path(datadir) / "agent_conf.yaml"
    if not conf_file.exists():
        raise FileNotFoundError(f"Agent configuration file not found: {conf_file}")
    with open(conf_file, "r") as f:
        conf = yaml.safe_load(f)
    return conf


class ShellBot3:
    def __init__(self, datadir: Path, thread_id: str = None):
        self.message_history = MessageHistory(datadir / "message_history.db")
        if thread_id is None:
            thread_id = self.message_history.get_most_recent_thread_id()
            if thread_id is None:
                thread_id = str(uuid.uuid4())
        self.thread_id = thread_id
        self.conf = load_conf(datadir)
        self.agent = self._initialize_agent(self.conf, create_tools())
    
    def _initialize_agent(self, conf, tools):
        return Agent(
            conf.get('model', 'google-gla:gemini-3-flash-preview'),
            instructions=(
                conf.get('instructions', "You are a friendly assistant")
            ),
            tools=tools,
        )

    async def run(self, prompt: str):
        recent_messages = ModelMessagesTypeAdapter.validate_python([
            msg['message']
            for msg in self.message_history.get_messages_starting_with_user_prompt(
                self.thread_id,
                limit=self.conf.get('recent_messages_limit', 10),
            )
        ])

        run_input = RunAgentInput(
            thread_id=self.thread_id,
            run_id=str(uuid.uuid4()),
            parent_run_id=None,
            state=None,
            messages=[UserMessage(id=str(uuid.uuid4()), content=prompt)],
            tools=[],
            context=[],
            forwarded_props=None,
        )

        runresult = None
        
        def on_complete(result: AgentRunResult):
            nonlocal runresult
            runresult = result
            new_messages = result.new_messages() if result else []
            new_messages_jsonable = to_jsonable_python(new_messages)
            self.message_history.add_messages(self.thread_id, new_messages_jsonable)

        adapter = AGUIAdapter(self.agent, run_input=run_input)
        async for event in adapter.run_stream(message_history=recent_messages, on_complete=on_complete):
            pprint(event.model_dump_json(indent=2))
        
        return runresult
