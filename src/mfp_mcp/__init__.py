"""
MyFitnessPal MCP Server

A Model Context Protocol (MCP) server for interacting with MyFitnessPal data.
"""

__version__ = "1.0.0"
__author__ = "Adam"

__all__ = ["mcp", "__version__"]


def __getattr__(name):
    # Lazy, so `python -m mfp_mcp.server` doesn't find the module already
    # imported by the package (which runs it twice and triggers a RuntimeWarning).
    if name == "mcp":
        from .server import mcp

        return mcp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
