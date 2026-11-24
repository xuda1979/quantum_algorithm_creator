"""Run the agent for a formalized reversible circuit contract."""

from __future__ import annotations

from dataclasses import dataclass, field

from .library import Candidate, KnownAlgorithmLibrary
from .novelty import InnovationCertificate
from .problem import IRKind, ProblemIR, TriageReport, triage
from .sandbox import VerificationReport, evaluate_candidate, verification_sizes
from .sota_baselines import specialized_baselines
from .track_b import DiscoveryResult, run_discovery


@dataclass
class PortfolioEntry:
    candidate_id: str
    track: str
    provenance: str
    report: VerificationReport
    score: tuple[int, ...]
    certificate: InnovationCertificate | None = None
    candidate: Candidate | None = None


@dataclass
class AgentResult:
    problem: str
    triage: TriageReport
    portfolio: list[PortfolioEntry] = field(default_factory=list)
    track_b_trace: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    discovery: DiscoveryResult | None = None

    @property
    def delivered(self) -> bool:
        return bool(self.portfolio)


def _entry(cand: Candidate, rep: VerificationReport, certificate=None) -> PortfolioEntry:
    provenance = "known" if certificate is None else "optimized"
    n = rep.sizes[-1]
    score = (rep.toffoli[n], rep.gate_count[n], rep.depth[n], rep.peak_qubits[n])
    return PortfolioEntry(cand.id, cand.track, provenance, rep, score, certificate, cand)


def run_agent(
    problem: str,
    ir: ProblemIR,
    sizes=(3, 4, 5),
    seed: int = 0,
    track_b_budget: int = 16,
    quantum_speedup_poly: int | None = None,
    *,
    holdout_sizes=None,
    evolve: bool = True,
) -> AgentResult:
    """Solve an explicitly registered reversible arithmetic contract."""
    tr = triage(ir, quantum_speedup_poly=quantum_speedup_poly)
    result = AgentResult(problem=problem, triage=tr)
    if not tr.recommend_quantum:
        return result

    library = KnownAlgorithmLibrary()
    key = ir.notes.get("library_key", ir.known_template)
    if ir.kind is not IRKind.ARITHMETIC or not isinstance(key, str) or not library.has(key):
        result.notes.append("No implemented reversible contract for this IR kind and library key.")
        return result
    if ir.known_template is not None and ir.known_template != key:
        result.notes.append("The known template and library key specify different contracts.")
        return result
    sizes = verification_sizes(sizes)

    if tr.track_b_mode is not None:
        discovery = run_discovery(sizes=sizes, max_batches=track_b_budget, seed=seed,
                                  mode=tr.track_b_mode, problem_key=key,
                                  holdout_sizes=holdout_sizes, evolve=evolve)
        result.discovery = discovery
        result.track_b_trace = discovery.trace
        for entry in discovery.certified:
            result.portfolio.append(_entry(entry.candidate, entry.report, entry.certificate))
        if not discovery.succeeded:
            result.notes.append("Track B finished without a certified resource improvement.")

    if tr.track_a or not result.portfolio:
        reference = library.synthesize(key, sizes[-1])
        specialized = specialized_baselines(library).get(key)
        for candidate in (reference, specialized):
            if candidate is None:
                continue
            rep = evaluate_candidate(candidate, sizes=sizes)
            if rep.passed:
                result.portfolio.append(_entry(candidate, rep))
            else:
                result.notes.append(f"{candidate.id} failed verification: {rep.failure_reasons[0]}")

    result.portfolio.sort(key=lambda entry: (entry.score, entry.candidate_id))
    return result
