"""L0 · Platform — the kernel's primitives and ports: Settings, the model vocabulary
(Message, ModelRequest/Response, Usage) and the ModelProvider port, Clock.

The innermost and most stable layer: it imports nothing else from this package
and no vendor SDK. Implementations of these ports live in ``adapters/``.
"""
