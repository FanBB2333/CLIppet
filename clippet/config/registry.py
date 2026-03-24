"""Configuration registry for CLIppet adapters."""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from clippet.adapters.claude import ClaudeAdapter
from clippet.adapters.codex import CodexAdapter
from clippet.adapters.qoder import QoderAdapter
from clippet.orchestrator import ClippetRunner


class AdapterConfig(BaseModel):
    """Configuration for a single adapter.
    
    Attributes:
        adapter_type: Type of adapter - "claude", "codex", or "qoder".
        name: Registration name for the adapter in the runner.
        options: Adapter constructor kwargs (model, etc.).
    """

    adapter_type: Literal["claude", "codex", "qoder"]
    name: str
    options: dict[str, Any] = {}


class ClippetConfig(BaseModel):
    """Top-level configuration for CLIppet.
    
    Attributes:
        adapters: List of adapter configurations.
        default_timeout: Default timeout in seconds for agent execution.
        max_workers: Maximum number of worker threads for parallel execution.
    """

    adapters: list[AdapterConfig]
    default_timeout: int = 900
    max_workers: int = 4


def load_config(path: Path) -> ClippetConfig:
    """Load and parse a YAML config file into ClippetConfig.
    
    Args:
        path: Path to the YAML configuration file.
        
    Returns:
        Parsed ClippetConfig object.
        
    Raises:
        FileNotFoundError: If the config file doesn't exist.
        yaml.YAMLError: If the YAML is invalid.
        pydantic.ValidationError: If the config doesn't match the schema.
    """
    with open(path, "r", encoding="utf-8") as f:
        raw_config = yaml.safe_load(f)
    
    return ClippetConfig(**raw_config)


def create_runner_from_config(config: ClippetConfig) -> ClippetRunner:
    """Create a ClippetRunner and register all adapters from config.
    
    Args:
        config: The ClippetConfig containing adapter definitions.
        
    Returns:
        Configured ClippetRunner with all adapters registered.
        
    Raises:
        ValueError: If an unknown adapter_type is specified.
    """
    # Mapping from adapter_type to adapter class
    adapter_classes = {
        "claude": ClaudeAdapter,
        "codex": CodexAdapter,
        "qoder": QoderAdapter,
    }
    
    runner = ClippetRunner(max_workers=config.max_workers)
    
    for adapter_config in config.adapters:
        adapter_class = adapter_classes.get(adapter_config.adapter_type)
        
        if adapter_class is None:
            raise ValueError(
                f"Unknown adapter type: '{adapter_config.adapter_type}'. "
                f"Valid types are: {list(adapter_classes.keys())}"
            )
        
        # Instantiate the adapter with options
        adapter = adapter_class(**adapter_config.options)
        
        # Register with the runner
        runner.register(adapter_config.name, adapter)
    
    return runner
