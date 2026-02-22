import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from shellbot2.agent import ShellBot3

@patch('shellbot2.agent.load_conf')
@patch('shellbot2.agent.MessageHistory')
@patch('shellbot2.agent.ShellBot3._initialize_agent')
def test_dynamic_tool_loading(mock_init_agent, mock_msg_history, mock_load_conf, tmp_path):
    # Configure mock
    mock_load_conf.return_value = {
        'tools': [
            'shell',
            {'document-store': {'store_id': 'test-store-id'}},
            'python'
        ]
    }
    
    # Initialize bot
    bot = ShellBot3(datadir=tmp_path)
    
    # Check that tools were created based on config
    # We mocked _initialize_agent, so we can see what tools were passed to it
    assert mock_init_agent.called
    tools_passed = mock_init_agent.call_args[0][1]
    
    # We should have exactly 3 tools loaded
    assert len(tools_passed) == 3
    
    tool_names = [tool.name for tool in tools_passed]
    assert 'shell' in tool_names
    assert 'python' in tool_names
    assert 'document-store' in tool_names

@patch('shellbot2.agent.load_conf')
@patch('shellbot2.agent.MessageHistory')
@patch('shellbot2.agent.ShellBot3._initialize_agent')
def test_custom_tool_loading(mock_init_agent, mock_msg_history, mock_load_conf, tmp_path):
    # Create custom tool dir and file
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    
    custom_tool_code = """
from shellbot2.tools.util import classproperty

class MyCustomTool:
    @property
    def name(self):
        return "my-custom-tool"
        
    @classproperty
    def toolname(cls):
        return "my-custom-tool"
        
    @property
    def description(self):
        return "A custom tool"
        
    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {},
            "required": []
        }
        
    def __call__(self, **kwargs):
        return "custom result"
"""
    (tools_dir / "my_custom_tool.py").write_text(custom_tool_code)
    
    # Configure mock
    mock_load_conf.return_value = {
        'tools': [
            'shell',
            'my-custom-tool'
        ]
    }
    
    # Initialize bot
    bot = ShellBot3(datadir=tmp_path)
    
    # Check that tools were created based on config
    assert mock_init_agent.called
    tools_passed = mock_init_agent.call_args[0][1]
    
    # We should have exactly 2 tools loaded
    assert len(tools_passed) == 2
    
    tool_names = [tool.name for tool in tools_passed]
    assert 'shell' in tool_names
    assert 'my-custom-tool' in tool_names
