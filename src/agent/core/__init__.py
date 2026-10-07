"""L2 · Core — the agent kernel: AgentRunner, ContextBuilder, ToolRegistry,
ToolExecutor + Policy (the single permission boundary), ApprovalGate.

Kernel only: no product code lives here. Must stay testable with no HTTP, no DB
and no real model. The core never imports
an edge or an adapter; it sees them only through the Protocols defined here.
"""
