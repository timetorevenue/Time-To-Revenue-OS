"""TTROS agent runtime: run TAP agent packages and plans in-process.

One function runs one agent (``run_agent``) and one runs a plan of agents
(``run_plan``). Both return the same result shape. The Work Queue stays the
only queue, ModelPort the only model path, the scoped Brain readers the only
readers and rules/runtime_permission_policy.md the only permission authority.

Revisit: when the TAP manifest schema, the result shape or the runner seam
changes. · Last touched: 2026-11-14.
"""

from .package import AgentPackage, PackageError, load_package, parse_ref  # noqa: F401
from .plans import PlanError, load_plan, run_plan  # noqa: F401
from .runtime import RESULT_STATUSES, RunEnv, run_agent  # noqa: F401

RUNTIME_VERSION = "0.1.0"
