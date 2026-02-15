"""Test helpers for memory module tests."""

from pathlib import Path

# Base path to memory module source files
MEMORY_MODULE_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "src" / "butlers" / "modules" / "memory"
)
