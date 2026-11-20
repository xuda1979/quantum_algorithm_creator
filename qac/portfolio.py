"""Layer 4: ranked solution portfolio + the end-to-end agent pipeline.

Ties Layers 1-3 together: triage -> (Track A and/or Track B in parallel)
-> sandbox verification of every candidate -> ranked portfolio with
provenance tags ("known" vs "invented"), per AGENT_DESIGN.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .problem import IRKind, ProblemIR, TriageReport, triage
from .library import KnownAlgorithmLibrary, Candidate, build_increment_ancilla_reference
from .sandbox import evaluate_candidate, VerificationReport
from .track_b import run_discovery, MutatorStandIn


@dataclass
class PortfolioEntry:
    candidate_id: str
    track: str
    provenance: str  # "known" | "invented"
    report: VerificationReport
    score: tuple  # (toffoli@max_n, gates@max_n, depth@max_n) — lower is better


@dataclass
class AgentResult:
    problem: str
    triage: TriageReport
    portfolio: list = field(default_factory=list)
    track_b_trace: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def delivered(self) -> bool:
        return len(self.portfolio) > 0


def _entry_score(rep: VerificationReport) -> tuple:
    if not rep.sizes:
        return (float("inf"),) * 3
    m = rep.sizes[-1]
    return (rep.toffoli.get(m, 10**9), rep.gate_count.get(m, 10**9), rep.depth.get(m, 10**9))


def run_agent(
    problem: str,
    ir: ProblemIR,
    sizes=(3, 4, 5),
    seed: int = 0,
    track_b_budget: int = 3,
    quantum_speedup_poly: int | None = None,
) -> AgentResult:
    """Full pipeline for one formalized problem."""
    result = AgentResult(problem=problem, triage=None)

    # ---- Layer 1: triage ------------------------------------------------
    tr = triage(ir, quantum_speedup_poly=quantum_speedup_poly)
    result.triage = tr
    if not tr.recommend_quantum:
        result.notes.append("Triage rejected the quantum route: classical wins.")
        return result

    lib = KnownAlgorithmLibrary()

    # ---- Track A: known template ----------------------------------------
    if tr.track_a:
        key = ir.notes.get("library_key", "increment")
        if lib.has(key):
            cand = lib.synthesize(key, sizes[-1])
            rep = evaluate_candidate(cand, sizes=sizes)
            if rep.passed:
                result.portfolio.append(
                    PortfolioEntry(cand.id, "A", "known", rep, _entry_score(rep))
                )
            else:
                result.notes.append(f"Track A candidate failed sandbox: {rep.failure_reasons}")
        else:
            result.notes.append(f"Track A: no library entry for '{key}'")

    # ---- Track B: invention (necessity or opportunity) --------------------
    if tr.track_b_mode is not None:
        discovery = run_discovery(
            mutator=MutatorStandIn(seed),
            sizes=sizes,
            max_batches=track_b_budget,
            seed=seed,
            mode="necessity" if tr.track_b_mode == "necessity" else "opportunity",
        )
        result.track_b_trace = discovery.trace
        for entry in discovery.invented:
            result.portfolio.append(
                PortfolioEntry(
                    entry.candidate.id, "B", "invented", entry.report, _entry_score(entry.report)
                )
            )
        if not discovery.succeeded:
            result.notes.append("Track B: invention budget exhausted without success (acceptable).")

    # ---- rank & tag ------------------------------------------------------
    result.portfolio.sort(key=lambda e: e.score)
    seen: set[str] = set()
    deduped = []
    for e in result.portfolio:
        if e.candidate_id in seen:
            continue
        seen.add(e.candidate_id)
        deduped.append(e)
    result.portfolio = deduped
    return result
