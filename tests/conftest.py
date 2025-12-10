"""Pytest fixtures for SC-REPL MCP Server tests."""

import pytest
from unittest.mock import Mock

from sc_repl_mcp.client import SCClient


@pytest.fixture
def client():
    """Provide a fresh SCClient instance for testing."""
    return SCClient()


@pytest.fixture
def mock_sc_client(mocker):
    """Provide a mock SCClient that's patched into the tools module."""
    mock = Mock(spec=SCClient)
    mocker.patch('sc_repl_mcp.tools.sc_client', mock)
    return mock
