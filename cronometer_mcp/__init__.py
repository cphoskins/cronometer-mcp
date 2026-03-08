"""Cronometer MCP Server — nutrition data from Cronometer via MCP."""

__version__ = "2.0.1"

from .client import CronometerClient
from .markdown import generate_food_log_md

__all__ = ["CronometerClient", "generate_food_log_md"]
