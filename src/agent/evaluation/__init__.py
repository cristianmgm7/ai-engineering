"""Cross-cutting · Evaluation harness (kernel) — measure model behaviour.

``EvalCase`` + ``ScriptedTool`` describe a scenario, ``graders`` score a run, and
``Harness`` runs cases × reps through the real loop and boundary with a real
model, then builds a ``Report`` with pass rates, cost and latency.

The outermost package: it may import every layer, and no layer imports it. The
cases themselves are product and live in the repo's top-level ``evals/``.
"""
