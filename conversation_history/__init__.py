"""Backend de memoria de conversación del agente (checkpointer de LangGraph)."""

from .conversation_history import get_checkpointer, close_checkpointer

__all__ = ["get_checkpointer", "close_checkpointer"]
