"""
Agent Base Class.

All VoxOS agents implement this interface.
Agents communicate through typed Input/Output Pydantic models.
The TaskManager calls agents — agents do NOT call each other directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from pydantic import BaseModel

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class BaseAgent(ABC, Generic[InputT, OutputT]):
    """Abstract base for all VoxOS agents."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Agent identifier (e.g. 'listener', 'strategist')."""

    @abstractmethod
    async def process(self, input: InputT) -> OutputT:
        """
        Process input and return typed output.

        Agents MUST NOT:
        - Execute tools directly
        - Call other agents directly
        - Block the event loop for more than a few seconds
        """
