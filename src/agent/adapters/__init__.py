"""L3 · Adapters — everything that talks to a vendor or the outside world.

- ``models/``: ``ModelProvider`` implementations (Anthropic).
- later ``stores/`` (SQLite, in-memory), ``tracing/`` (Langfuse).
- a product subpackage (``whatsapp/``): product tools and Policy rules; an
  ``MCPClient`` lives inside an adapter, never in the core.

Kernel adapters sit at the top level; product code goes in the product subpackage.
"""
