"""Layer 1: problem formalization & classical triage (lightweight stand-in).

Full design (AGENT_DESIGN.md Layer 1) uses a Domain-to-Math LLM agent plus
classical baselines (CP-SAT/Gurobi, tensor networks, Monte Carlo). This
module implements the deterministic skeleton: typed IR dataclasses, the
complexity-vs-classical triage rule, and the known-template router that
decides Track A (known algorithm), Track B (invent), or both in parallel
(necessity / opportunity modes).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class IRKind(Enum):
    HUBO = "hubo"            # higher-order unconstrained binary optimization
    ISING = "ising"          # quadratic Ising Hamiltonian
    LINEAR_SYSTEM = "linear_system"  # sparse matrix A x = b
    SIMULATION = "simulation"  # Hamiltonian simulation / chemistry
    ORACLE_SEARCH = "oracle_search"  # unstructured search with oracle f
    ARITHMETIC = "arithmetic"  # reversible arithmetic subroutine
    PHASE_ESTIMATION = "phase_estimation"


@dataclass
class ProblemIR:
    """Typed mathematical intermediate representation (Layer-1 output)."""

    kind: IRKind
    size_expr: str = "n"  # symbolic size parameter
    known_template: str | None = None  # routed BQP template name, if any
    data_loading: str = "programmatic"  # "programmatic" | "qram"
    classical_complexity: str = ""  # e.g. "O(2^n)", "poly(n)"
    notes: dict = field(default_factory=dict)


@dataclass
class TriageReport:
    """Result of the Complexity & Novelty Router."""

    recommend_quantum: bool
    track_a: bool  # known template applies
    track_b_mode: str | None  # "necessity" | "opportunity" | None
    reasons: list = field(default_factory=list)


def triage(ir: ProblemIR, quantum_speedup_poly: int | None = None) -> TriageReport:
    """Apply the triage gate.

    Rules (reduced but faithful):
      1. One-off QRAM load with only quadratic speedup -> reject.
      2. No known template -> Track B necessity mode.
      3. Known template but resource bottleneck flagged -> Track B opportunity
         alongside Track A (one problem, many solutions).
      4. Otherwise Track A only.
    """
    reasons: list[str] = []
    quadratic_only = quantum_speedup_poly == 2
    if ir.data_loading == "qram" and quadratic_only:
        reasons.append(
            "Under the one-off O(N) loading model, a quadratic query speedup "
            "does not yield a sublinear end-to-end algorithm."
        )
        return TriageReport(False, False, None, reasons)

    if ir.known_template is None:
        reasons.append("No known BQP template fits: necessity mode — attempt invention.")
        return TriageReport(True, False, "necessity", reasons)

    if ir.notes.get("resource_bottleneck"):
        reasons.append(
            f"Known template '{ir.known_template}' has a flagged bottleneck: "
            "search for improved implementations alongside the known circuit."
        )
        return TriageReport(True, True, "opportunity", reasons)

    reasons.append(f"Known template '{ir.known_template}' applies within budget.")
    return TriageReport(True, True, None, reasons)
