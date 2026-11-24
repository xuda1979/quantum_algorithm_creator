"""Finite correctness, resource comparisons, and structural deduplication."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .kickmix import CCZ, CZ, SWAP, Circuit, KickmixState, Toffoli
from .sandbox import check_semantics, evaluate_candidate, expected_outputs, verification_sizes


RESOURCE_AXES = ("width", "toffoli", "gates", "depth")


def _resources(circuit: Circuit) -> dict[str, int]:
    return dict(zip(RESOURCE_AXES, (circuit.n, circuit.toffoli_count(), circuit.gate_count(), circuit.depth())))


def _fingerprint(circuit: Circuit) -> tuple:
    """Ignore ordering of disjoint gates and of symmetric controls."""
    available = [0] * circuit.n
    signature = []
    for gate in circuit.gates:
        wires = gate.targets()
        depth = 1 + max((available[q] for q in wires), default=0)
        for q in wires:
            available[q] = depth
        if isinstance(gate, Toffoli):
            wires = (*sorted(wires[:-1]), wires[-1])
        elif isinstance(gate, (CZ, CCZ, SWAP)):
            wires = tuple(sorted(wires))
        signature.append((depth, gate.name, wires))
    return circuit.n, tuple(sorted(signature))


def _kickmix_check(circuit: Circuit, outputs: tuple[int, ...]) -> bool:
    for x, y in enumerate(outputs):
        state = KickmixState(circuit.n, x)
        for gate in circuit.gates:
            gate.apply_kickmix(state)
        if state.x != y or state.phase != 0:
            return False
    return True


@dataclass
class InnovationCertificate:
    """Evidence about a circuit family relative to an explicit baseline set.

    `novel` is the legacy field for structural distinctness within that set.
    Correct implementations share the same clean-ancilla logical action.
    """

    candidate_id: str
    correct: bool = False
    valuable: bool = False
    novel: bool = False
    certified: bool = False
    extended_sizes: list[int] = field(default_factory=list)
    kickmix_sizes: list[int] = field(default_factory=list)
    statevector_sizes: list[int] = field(default_factory=list)
    baselines_compared: list[str] = field(default_factory=list)
    pareto_strict_axes: list[str] = field(default_factory=list)
    dominated_baselines: list[str] = field(default_factory=list)
    resources: dict[int, dict[str, int]] = field(default_factory=dict)
    baseline_resources: dict[str, dict[int, dict[str, int]]] = field(default_factory=dict)
    improved_sizes: dict[str, list[int]] = field(default_factory=dict)
    novelty_verdict: str = "unverified"
    literature_status: str = "unassessed"
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _same_spec(candidate, baseline, sizes=(3, 4, 5)) -> bool:
    """Check the declared contract for every size, including callable specs."""
    for n in sizes:
        data_n = candidate.n_data(n)
        if data_n != baseline.n_data(n):
            return False
        if expected_outputs(candidate.spec(n), data_n) != expected_outputs(baseline.spec(n), data_n):
            return False
    return True


def certify_innovation(
    candidate,
    report=None,
    baseline_candidates: list | None = None,
    extended_sizes=(1, 2, 3, 4, 5, 6),
    dense_crosscheck_max_width: int = 8,
) -> InnovationCertificate:
    """Check all declared sizes and require a measurable win over each baseline.

    A Pareto win allows ties at some sizes (including trivial base cases),
    requires weak dominance on every measured axis and size, and requires
    a strict gain somewhere against each baseline. With no matched baseline
    the result is correct-only evidence. Literature novelty needs review.
    """
    cert = InnovationCertificate(candidate_id=candidate.id)
    try:
        sizes = verification_sizes(extended_sizes)
        if report is not None:
            if report.candidate_id != candidate.id or not report.passed:
                cert.reasons.append("Layer-3 report is failing or belongs to a different candidate")
                return cert
            sizes = verification_sizes((*sizes, *report.sizes))
    except ValueError as exc:
        cert.reasons.append(str(exc))
        return cert

    verification = evaluate_candidate(candidate, sizes=sizes)
    if not verification.passed:
        cert.reasons.extend(verification.failure_reasons)
        return cert

    circuits = {}
    try:
        for n in sizes:
            circuit = candidate.build(n)
            circuit.validate()
            data_n = candidate.n_data(n)
            outputs = expected_outputs(candidate.spec(n), data_n)
            if verification.semantic_backend[n] == "packed":
                if not _kickmix_check(circuit, outputs):
                    cert.reasons.append(f"n={n}: independent Kickmix check disagrees")
                    return cert
                cert.kickmix_sizes.append(n)
            if (verification.semantic_backend[n] == "statevector"
                    or circuit.n <= dense_crosscheck_max_width):
                if verification.semantic_backend[n] != "statevector":
                    result = check_semantics(circuit, outputs.__getitem__, data_n)
                    if not result.ok:
                        cert.reasons.append(f"n={n}: dense statevector check disagrees: {result.reasons[0]}")
                        return cert
                cert.statevector_sizes.append(n)
            circuits[n] = circuit
            cert.extended_sizes.append(n)
            cert.resources[n] = _resources(circuit)
    except Exception as exc:
        cert.reasons.append(f"verification raised {type(exc).__name__}: {exc}")
        return cert
    cert.correct = True

    if baseline_candidates is None:
        from .library import KnownAlgorithmLibrary
        from .sota_baselines import sota_baselines, specialized_baselines

        library = KnownAlgorithmLibrary()
        baseline_candidates = [*library.candidates(), *sota_baselines(), *specialized_baselines(library).values()]

    strict_axes: set[str] | None = None
    duplicates = []
    for baseline in baseline_candidates:
        try:
            if not _same_spec(candidate, baseline, sizes):
                continue
            cert.baselines_compared.append(baseline.id)
            checked = evaluate_candidate(baseline, sizes=sizes, require_poly=False)
            if not checked.passed:
                raise ValueError(f"baseline verification failed: {checked.failure_reasons[0]}")
            references = {n: baseline.build(n) for n in sizes}
            profiles = {n: _resources(circuit) for n, circuit in references.items()}
        except Exception as exc:
            cert.reasons.append(f"could not compare baseline '{baseline.id}': {exc}")
            return cert

        cert.baseline_resources[baseline.id] = profiles
        if all(_fingerprint(circuits[n]) == _fingerprint(references[n]) for n in sizes):
            duplicates.append(baseline.id)
        regressions = []
        gains = set()
        improved = []
        for n in sizes:
            better = []
            for axis in RESOURCE_AXES:
                mine, theirs = cert.resources[n][axis], profiles[n][axis]
                if mine > theirs:
                    regressions.append((n, axis, mine, theirs))
                elif mine < theirs:
                    better.append(axis)
            if better:
                gains.update(better)
                improved.append(n)
        cert.improved_sizes[baseline.id] = improved
        if regressions:
            n, axis, mine, theirs = regressions[0]
            cert.reasons.append(f"baseline '{baseline.id}': {axis} regresses at n={n} ({mine} > {theirs})")
        elif not improved:
            cert.reasons.append(f"baseline '{baseline.id}': equal resource profiles on all checked sizes")
        else:
            cert.dominated_baselines.append(baseline.id)
            strict_axes = gains if strict_axes is None else strict_axes & gains

    if not cert.baselines_compared:
        cert.novelty_verdict = "unbenchmarked"
        cert.reasons.append("no verified same-contract baseline was supplied or found")
        return cert

    cert.valuable = len(cert.dominated_baselines) == len(cert.baselines_compared)
    cert.pareto_strict_axes = sorted(strict_axes or ()) if cert.valuable else []
    cert.novel = not duplicates
    cert.novelty_verdict = "distinct" if cert.novel else f"duplicate_of:{duplicates[0]}"
    if duplicates:
        cert.reasons.append(f"same normalized gate schedule as '{duplicates[0]}' at all checked sizes")
    cert.certified = cert.correct and cert.valuable and cert.novel
    return cert
