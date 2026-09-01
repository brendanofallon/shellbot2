
from functools import wraps
import inspect
from pathlib import Path
import traceback
from sqlalchemy.util.typing import NoneType
import yaml
import uuid
import logging
import boto3
import os

from pydantic import BaseModel
from pydantic_core import to_jsonable_python
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.providers.bedrock import BedrockProvider
from ag_ui.core import CustomEvent, RunAgentInput, UserMessage
from pydantic_ai.ui.ag_ui import AGUIAdapter
from pydantic_ai import (
    Agent,
    AgentRunResult,
    Tool,
    ModelMessagesTypeAdapter
)
from pydantic_ai.messages import ModelRequest

from pydantic_ai.providers.anthropic import AnthropicProvider

from shellbot2.azure_provider import create_azure_chat_model

from shellbot2.tools.botfunctions import ShellFunction, ReaderFunction, ClipboardFunction, PythonFunction, TavilySearchFunction
from shellbot2.context_compaction import ContextCompactionConfig, compact_recent_interactions
from shellbot2.database import database_path
from shellbot2.message_history import MessageHistory
from shellbot2.event_dispatcher import EventDispatcher, create_rich_output_dispatcher
from shellbot2.tools.discovery import discover_tool_specs
from shellbot2.tools.tool_spec import ToolRuntime, ToolSpec

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
    if inspect.iscoroutinefunction(func):

        @wraps(func)
        async def async_wrapper(**kwargs):
            try:
                return await func(**kwargs)
            except Exception as e:
                error_type = type(e).__name__
                error_msg = str(e)
                tb = traceback.format_exc()
                logger.error(f"Error in tool '{tool_name}': {error_type}: {error_msg}\n{tb}")
                return f"Error executing tool '{tool_name}': {error_type}: {error_msg}"

        return async_wrapper

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


def create_tool_from_spec(tool_spec: ToolSpec, tool_instance):
    return Tool.from_schema(
        function=safe_tool_call(tool_instance.__call__, tool_spec.model_name),
        name=tool_spec.model_name,
        description=tool_spec.description,
        json_schema=tool_spec.parameters,
        takes_ctx=False,
    )


def load_conf(datadir: Path):
    conf = {}
    conf_file = Path(datadir) / "agent_conf.yaml"
    if not conf_file.exists():
        raise FileNotFoundError(f"Agent configuration file not found: {conf_file}")
    with open(conf_file, "r") as f:
        conf = yaml.safe_load(f)
    return conf

def verify_aws_credentials(boto_session: boto3.Session, profile_name: str):
    """Verify that AWS credentials are present and not expired via a lightweight STS call."""
    credentials = boto_session.get_credentials()
    if credentials is None:
        raise RuntimeError(
            f"Could not resolve AWS credentials from profile '{profile_name}'. "
            f"Make sure you've run: aws sso login --profile {profile_name}"
        )
    frozen_credentials = credentials.get_frozen_credentials()
    if not frozen_credentials.access_key or not frozen_credentials.secret_key:
        raise RuntimeError(
            f"AWS credentials from profile '{profile_name}' are incomplete (missing access key or secret key). "
            f"Try: aws sso login --profile {profile_name}"
        )
    sts = boto_session.client("sts")
    sts.get_caller_identity()
    return frozen_credentials


def initialize_bedrock_model(model: str, region_name: str = 'us-west-2', aws_profile: str = "BedrockAPI-Access-470052372761", aws_region: str = 'us-west-2'):
    boto_session = boto3.Session(profile_name=aws_profile, region_name=aws_region)
    frozen_credentials = verify_aws_credentials(boto_session, aws_profile)
    provider = BedrockProvider(
        region_name=region_name,
        aws_access_key_id=frozen_credentials.access_key,
        aws_secret_access_key=frozen_credentials.secret_key,
        aws_session_token=frozen_credentials.token,
    )
    return BedrockConverseModel(model, provider=provider)


class ShellBot3:
    def __init__(self, datadir: Path, thread_id: str = None, event_dispatcher: EventDispatcher = None):
        self.datadir = Path(datadir)
        self.message_history = MessageHistory(database_path(self.datadir))
        if thread_id is None:
            thread_id = self.message_history.get_most_recent_thread_id()
            if thread_id is None:
                thread_id = str(uuid.uuid4())
        self.thread_id = thread_id
        self.conf = load_conf(self.datadir)
        logger.info(f"Config: {self.conf}")
        tools = self._create_tools()
        self.agent = self._initialize_agent(self.conf, tools)
        self.event_dispatcher = event_dispatcher

    def _load_tool(self, tool_entry: str | dict, available_specs: dict[str, ToolSpec], runtime: ToolRuntime) -> Tool | None:
        """
        Load a single tool from a tool entry (either a bare string or a single element dict with a tool name and one or more kwargs as values)
        Returns either a Tool or None if the entry was not valid.
        """
        if isinstance(tool_entry, str):
            tool_name = tool_entry
            tool_kwargs: dict = {}
        elif isinstance(tool_entry, dict) and len(tool_entry) == 1:
            tool_name, tool_kwargs = next(iter(tool_entry.items()))
            if tool_kwargs is None:
                tool_kwargs = {}
            elif not isinstance(tool_kwargs, dict):
                logger.warning(
                    "Tool %r configuration must be a mapping, got %s.",
                    tool_name,
                    type(tool_kwargs).__name__,
                )
                return None
        else:
            logger.warning(f"Invalid tool configuration entry: {tool_entry}")
            return None

        tool_spec = available_specs.get(tool_name)
        if tool_spec is None:
            logger.warning(f"Tool '{tool_name}' requested in config but not found.")
            return None

        try:
            tool_instance = tool_spec.factory(runtime, tool_kwargs)
            if not callable(tool_instance):
                raise TypeError(
                    f"Tool factory for '{tool_name}' returned a non-callable "
                    f"{type(tool_instance).__name__}"
                )
            tool = create_tool_from_spec(tool_spec, tool_instance)
            logger.debug(f"Initialized tool '{tool_name}'")
            return tool
        except Exception as e:
            logger.exception(f"Failed to initialize tool '{tool_name}'")


    def _create_tools(self):
        available_specs = discover_tool_specs(self.datadir / "tools")
        configured_tools = self.conf.get("tools")
        if not configured_tools:
            logger.warning("No 'tools' section in agent_conf.yaml. Loading all discovered tools.")
            configured_tools = list(available_specs)

        runtime = ToolRuntime(
            datadir=self.datadir,
            config=self.conf,
            message_history=self.message_history,
        )
        tools = []
        for tool_entry in configured_tools:
            tool = self._load_tool(tool_entry, available_specs, runtime)
            if tool != None:
                tools.append(tool)
                logger.info(f"Loaded tool {tool_entry}")
            else:
                raise ValueError(f"Could not load tool {tool_entry}, aborting")

        return tools


    def _initialize_agent(self, conf, tools):
        instructions = conf.get("instructions", "You are a friendly assistant")
        logger.info(f"Creating agent with model {conf.get('model')}")
        if conf.get("provider") == "bedrock":
            bedrock_conf = conf.get("bedrock", {})
            model = initialize_bedrock_model(conf.get("model"), aws_profile=bedrock_conf.get("profile", "BedrockAPI-Access-470052372761"), aws_region=bedrock_conf.get('region', 'us-west-2') )
            return Agent(model=model, instructions=instructions, tools=tools)

        elif conf.get("provider") == "azure" or (
            isinstance(conf.get("model"), str) and conf.get("model").startswith("azure:")
        ):
            model_name = conf.get("model")
            if not model_name or "gpt" not in model_name:
                raise ValueError("Only GPT models supported in Azure now")
            logger.info(f"Creating model azure: {model_name}")
            return Agent(
                model=create_azure_chat_model(
                    model_name, conf, settings={"reasoning_effort": "medium"}
                ),
                instructions=instructions,
                tools=tools,
            )
        elif conf.get("provider") == "openrouter":
            return Agent(
                f"openrouter:{conf.get('model')}",
                instructions=instructions,
                tools=tools,
            )
        else:
            return Agent(
                conf.get("model"),
                instructions=instructions,
                tools=tools,
            )

    def _get_context(self):
        interactions = self.message_history.get_recent_interactions(
            self.thread_id,
            limit=self.conf.get('recent_messages_limit', 5),
            messages_only=False,
        )
        compaction_config = ContextCompactionConfig.from_dict(
            self.conf.get("context_compaction", {}) or {}
        )
        compacted_messages = compact_recent_interactions(interactions, compaction_config)
        recent_messages = ModelMessagesTypeAdapter.validate_python(compacted_messages)
        return recent_messages

    async def run(self, prompt: str):
        logger.info(f"Running prompt: {prompt[0:100]}...")
        recent_messages = self._get_context()

        if self.event_dispatcher and recent_messages:
            context_json = ModelMessagesTypeAdapter.dump_json(recent_messages)
            self.event_dispatcher.dispatch(CustomEvent(
                name="context_token_estimate",
                value=len(context_json) // 4,
            ))

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
