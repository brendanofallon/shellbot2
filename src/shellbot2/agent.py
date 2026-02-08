
from functools import wraps
from pathlib import Path
import traceback
import yaml
import uuid
import logging
import boto3

from pydantic import BaseModel
from pydantic_core import to_jsonable_python
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.providers.bedrock import BedrockProvider
from ag_ui.core import RunAgentInput, UserMessage
from pydantic_ai.ui.ag_ui import AGUIAdapter
from pydantic_ai import (
    Agent, 
    AgentRunResult,
    Tool, 
    ModelMessagesTypeAdapter
)
from pydantic_ai.messages import ModelRequest
        
from shellbot2.tools.botfunctions import ShellFunction, ReaderFunction, ClipboardFunction, PythonFunction, TavilySearchFunction
from shellbot2.message_history import MessageHistory
from shellbot2.event_dispatcher import EventDispatcher, create_rich_output_dispatcher
from shellbot2.tools.fastmailtool import FastmailTool
from shellbot2.tools.cal import CalendarTool
from shellbot2.tools.imagetool import ImageTool
from shellbot2.tools.memorytool import MemoryFunction
from shellbot2.tools.docstoretool import DocStoreTool 
from shellbot2.tools.conversationsearchtool import ConversationSearchTool

logger = logging.getLogger(__name__)


class BedrockConfig(BaseModel):
    """Configuration for AWS Bedrock model. Used when provider is 'bedrock'."""

    model: str = "anthropic.claude-3-5-sonnet-v1:0"
    region_name: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None

    def to_bedrock_model(self) -> BedrockConverseModel:
        provider_kwargs: dict = {
            "region_name": self.region_name,
            "aws_access_key_id": self.aws_access_key_id,
            "aws_secret_access_key": self.aws_secret_access_key,
            "aws_session_token": self.aws_session_token,
        }
        provider = BedrockProvider(**{k: v for k, v in provider_kwargs.items() if v is not None})
        return BedrockConverseModel(self.model, provider=provider)


def safe_tool_call(func, tool_name: str):
    """Wrap a tool function to catch exceptions and return error messages.
    
    This prevents exceptions from propagating up and crashing the agent run.
    Instead, errors are returned as text messages that the model can interpret.
    
    Args:
        func: The tool's __call__ method to wrap.
        tool_name: Name of the tool for error messages.
        
    Returns:
        A wrapped function that catches exceptions and returns error text.
    """
    @wraps(func)
    def wrapper(**kwargs):
        try:
            return func(**kwargs)
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            tb = traceback.format_exc()
            logger.error(f"Error in tool '{tool_name}': {error_type}: {error_msg}\n{tb}")
            return f"Error executing tool '{tool_name}': {error_type}: {error_msg}"
    return wrapper


def create_tool_from_schema(tool_cls):
    return Tool.from_schema(
        function=safe_tool_call(tool_cls.__call__, tool_cls.name),
        name=tool_cls.name,
        description=tool_cls.description,
        json_schema=tool_cls.parameters,
        takes_ctx=False,
    )

def create_tools():
    return [
        create_tool_from_schema(ShellFunction()),
        create_tool_from_schema(ReaderFunction()),
        #   create_tool_from_schema(FastmailTool()),
        #        create_tool_from_schema(CalendarTool()),
        create_tool_from_schema(ImageTool()),
        create_tool_from_schema(MemoryFunction()),
        create_tool_from_schema(DocStoreTool()),
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

def initialize_bedrock_model(model: str, region_name: str = 'us-west-2', aws_profile: str = "BedrockAPI-Access-470052372761", aws_region: str = 'us-west-2'):
    
    # Create a boto3 session with the specified profile to get SSO credentials
    boto_session = boto3.Session(profile_name=aws_profile, region_name=aws_region)
    
    # Get credentials from the session
    credentials = boto_session.get_credentials()
    if credentials is None:
        raise RuntimeError(
            f"Could not resolve AWS credentials from profile '{aws_profile}'. "
            f"Make sure you've run: aws sso login --profile {aws_profile}"
        )
    
    # Get frozen credentials (resolves any refresh tokens)
    frozen_credentials = credentials.get_frozen_credentials()
    provider = BedrockProvider(
        region_name=region_name,
        aws_access_key_id=frozen_credentials.access_key,
        aws_secret_access_key=frozen_credentials.secret_key,
        aws_session_token=frozen_credentials.token,
    )
    return BedrockConverseModel(model, provider=provider)

class ShellBot3:
    def __init__(self, datadir: Path, thread_id: str = None, event_dispatcher: EventDispatcher = None):
        self.message_history = MessageHistory(datadir / "message_history.db")
        if thread_id is None:
            thread_id = self.message_history.get_most_recent_thread_id()
            if thread_id is None:
                thread_id = str(uuid.uuid4())
        self.thread_id = thread_id
        self.conf = load_conf(datadir)
        logger.info(f"Config: {self.conf}")
        tools = create_tools()
        tools.append(create_tool_from_schema(ConversationSearchTool(message_history=self.message_history)))
        self.agent = self._initialize_agent(self.conf, tools)
        self.event_dispatcher = event_dispatcher
    
    def _initialize_agent(self, conf, tools):
        instructions = conf.get("instructions", "You are a friendly assistant")
        if conf.get("provider") == "bedrock":
            bedrock_conf = conf.get("bedrock", {})
            model = initialize_bedrock_model(conf.get("model"), bedrock_conf.get('region_name', 'us-west-2'))
            return Agent(model=model, instructions=instructions, tools=tools)
        else:
            return Agent(
                conf.get("model", "google-gla:gemini-3-flash-preview"),
                instructions=instructions,
                tools=tools,
            )

    async def run(self, prompt: str):
        logger.info(f"Running prompt: {prompt[0:100]}...")
        recent_messages = ModelMessagesTypeAdapter.validate_python([
            msg.message
            for msg in self.message_history.get_recent_interactions(
                self.thread_id,
                limit=self.conf.get('recent_messages_limit', 5),
                messages_only=True,
            )
        ])
        user_message = UserMessage(id=str(uuid.uuid4()), content=prompt)
        run_input = RunAgentInput(
            thread_id=self.thread_id,
            run_id=str(uuid.uuid4()),
            parent_run_id=None,
            state=None,
            messages=[user_message],
            tools=[],
            context=[],
            forwarded_props=None,
        )

        runresult = None
        
        def on_complete(result: AgentRunResult):
            nonlocal runresult
            try:
                runresult = result
                user_model_message = ModelRequest.user_text_prompt(prompt)
                new_messages = [user_model_message] + (result.new_messages() if result else [])
                new_messages = [to_jsonable_python(m) for m in new_messages]
                self.message_history.add_interaction(self.thread_id, new_messages)
            except Exception as e:
                import traceback, sys
                tb = traceback.TracebackException(type(e), e, e.__traceback__)
                last_tb = e.__traceback__
                lineno = last_tb.tb_lineno if last_tb is not None else 'unknown'
                filename = last_tb.tb_frame.f_code.co_filename if last_tb is not None else 'unknown'
                stack = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
                logger.error(
                    f"ERROR: in on_complete (File \"{filename}\", line {lineno}): {e}\nStack trace:\n{stack}",
                    exc_info=True
                )
                raise e

        adapter = AGUIAdapter(self.agent, run_input=run_input)
        async for event in adapter.run_stream(message_history=recent_messages, on_complete=on_complete):
            logger.debug(f"Event: {event}")
            self.event_dispatcher.dispatch(event)

        if runresult is None:
            logger.error("Run result is None :(, something went wrong)")
            return None

        if runresult and runresult.usage():
            usage = runresult.usage()
            logger.info(
                f"Token usage - Request: {usage.request_tokens}, "
                f"Response: {usage.response_tokens}, "
                f"Total: {usage.total_tokens}"
            )
        
        return runresult
