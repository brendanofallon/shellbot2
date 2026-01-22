
from dataclasses import dataclass
import asyncio
from pathlib import Path
import yaml
import json
import random
import os

from pydantic_core import to_jsonable_python
from pydantic_ai import (
    Agent, 
    RunContext, 
    AgentRunResultEvent, 
    PartDeltaEvent, 
    TextPartDelta, 
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
        # create_tool_from_schema(ReaderFunction()),
        # create_tool_from_schema(ClipboardFunction()),
        # create_tool_from_schema(PythonFunction()),
        # create_tool_from_schema(TavilySearchFunction()),
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
    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        self.message_history = MessageHistory(Path(os.getenv("SB3_DATADIR", "~/.shellbot3")).expanduser() / "message_history.db")
        self.conf = load_conf(Path(os.getenv("SB3_DATADIR", "~/.shellbot3")).expanduser())
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
            for msg in self.message_history.get_messages(self.thread_id, limit=self.conf.get('recent_messages_limit', 10))
        ])

        async for event in self.agent.run_stream_events(
                prompt,
                message_history=recent_messages
        ):
            if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                print(event.delta.content_delta, end='', flush=True)
            elif isinstance(event, AgentRunResultEvent):
                runresult = event.result
        new_messages = runresult.new_messages() if runresult else None
        new_messages_jsonable = to_jsonable_python(new_messages)
        self.message_history.add_messages(self.thread_id, new_messages_jsonable)
        return runresult


async def main():
    agent = ShellBot3(thread_id='test')
    runresult = await agent.run('Awesome, which ones are the biggest?')


if __name__ == '__main__':
    asyncio.run(main())