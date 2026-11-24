"""Track B: bounded, evaluator-guided search over circuit generator programs."""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field, replace

from .arithmetic import SumProgram, build_sum_threshold, compact_circuit, threshold_flag
from .kickmix import CNOT, Circuit, Toffoli, X
from .library import Candidate, KnownAlgorithmLibrary, lessthan_k, majority_K, weighted_theta
from .novelty import InnovationCertificate, RESOURCE_AXES, certify_innovation
from .sandbox import VerificationReport, evaluate_candidate, verification_sizes
from .sota_baselines import sota_baselines, specialized_baselines


# ---------------------------------------------------------------------------
# Mutator: proposes parameterized generator programs (size n is an argument)
# ---------------------------------------------------------------------------

def _lean_interleaved_increment(n: int) -> Circuit:
    """Historical lean interleaved incrementer.

    Key idea: the library's textbook incrementer dedicates an ancilla slot to
    A_1 = b_0, but A_1 is just b_0 itself — the slot is pure waste. Drop it:
    b_1 reads b_0 directly, and A_2 = b_0 & b_1 starts the slot chain. Then
    interleave apply/uncompute top-down (pebble-game style qubit scheduling,
    design section 2.2) so each slot lives only as long as needed, and put
    X(0) strictly last so every uncompute reads the ORIGINAL b_0/b_1.

    Verified exhaustively n=1..6 (sandbox + brute force):
      width 2n-2 vs library 2n-1  (one fewer qubit),
      same 2n-4 Toffolis, gates/depth 3n-4 vs 3n-2.
    """
    if n == 1:
        return Circuit(1, [X(0)])
    if n == 2:
        return Circuit(2, [CNOT(0, 1), X(0)])
    total = 2 * n - 2
    c = Circuit(total)
    slot = lambda i: n + i - 2  # slot for A_i, i >= 2
    c.gates.append(Toffoli(0, 1, slot(2)))  # A_2 = b_0 & b_1
    for i in range(3, n):  # A_i = A_{i-1} & b_{i-1}
        c.gates.append(Toffoli(slot(i - 1), i - 1, slot(i)))
    for i in range(n - 1, 2, -1):  # top-down apply + uncompute
        c.gates.append(CNOT(slot(i), i))
        c.gates.append(Toffoli(slot(i - 1), i - 1, slot(i)))
    c.gates.append(CNOT(slot(2), 2))  # b_2 ^= A_2
    c.gates.append(Toffoli(0, 1, slot(2)))  # uncompute A_2
    c.gates.append(CNOT(0, 1))  # b_1 ^= A_1 = b_0
    c.gates.append(X(0))  # b_0 ^= 1 LAST
    return c


def _topwrite_increment(n: int) -> Circuit:
    """Historical top-write incrementer with a fused terminal carry."""
    if n == 1:
        return Circuit(1, [X(0)])
    if n == 2:
        return Circuit(2, [CNOT(0, 1), X(0)])
    if n == 3:
        # No slot survives: the only carry A_2 = b_0&b_1 goes straight into b_2.
        return Circuit(3, [Toffoli(0, 1, 2), CNOT(0, 1), X(0)])
    total = 2 * n - 3
    c = Circuit(total)
    slot = lambda i: n + i - 2  # slot for A_i, i = 2..n-2 (n-3 slots)
    c.gates.append(Toffoli(0, 1, slot(2)))  # A_2 = b_0 & b_1
    for i in range(3, n - 1):  # A_i = A_{i-1} & b_{i-1}, up to A_{n-2}
        c.gates.append(Toffoli(slot(i - 1), i - 1, slot(i)))
    c.gates.append(Toffoli(slot(n - 2), n - 2, n - 1))  # TOP-WRITE: b_{n-1} ^= A_{n-1}
    for i in range(n - 2, 2, -1):  # top-down apply + uncompute
        c.gates.append(CNOT(slot(i), i))
        c.gates.append(Toffoli(slot(i - 1), i - 1, slot(i)))
    c.gates.append(CNOT(slot(2), 2))  # b_2 ^= A_2
    c.gates.append(Toffoli(0, 1, slot(2)))  # uncompute A_2
    c.gates.append(CNOT(0, 1))  # b_1 ^= A_1 = b_0
    c.gates.append(X(0))  # b_0 ^= 1 LAST
    return c


def _zigzag_increment(n: int) -> Circuit:
    """Mutation A: ancilla-free zig-zag (CNOT/X layers) — plausible family
    member; the sandbox decides. (Negative control: gets rejected.)"""
    if n == 1:
        return Circuit(1, [X(0)])
    c = Circuit(n)
    for i in range(1, n):
        c.gates.append(CNOT(i - 1, i))
        c.gates.append(X(i))
    for i in range(n - 1, 0, -1):
        c.gates.append(X(i))
        c.gates.append(CNOT(i - 1, i))
    return c


def _cyclic_increment(n: int) -> Circuit:
    """Mutation B: cyclic-carry flavor (negative control: rejected)."""
    c = Circuit(n)
    c.gates.append(X(0))
    for i in range(1, n):
        c.gates.append(X(i))
    for i in range(n - 1, 0, -1):
        c.gates.append(CNOT(i - 1, i))
        c.gates.append(X(i))
    return c


def _inplace_lookahead_increment(n: int) -> Circuit:
    """Mutation C: in-place prefix-AND chain (negative control: rejected —
    in-place AND overwrites data bits that later stages still need)."""
    if n == 1:
        return Circuit(1, [X(0)])
    c = Circuit(n)
    for i in range(2, n):
        c.gates.append(Toffoli(i - 2, i - 1, i))
    c.gates.append(X(0))
    for i in range(n - 1, 0, -1):
        c.gates.append(X(i))
    for i in range(n - 1, 1, -1):
        c.gates.append(Toffoli(i - 2, i - 1, i))
    return c


GENERATOR_POOL = [
    _lean_interleaved_increment,
    _topwrite_increment,
    _zigzag_increment,
    _cyclic_increment,
    _inplace_lookahead_increment,
]


class MutatorStandIn:
    """Replay the historical increment proposals for auditing or custom tests."""

    def __init__(self, seed: int = 0, pool=None):
        self._pool = list(pool if pool is not None else GENERATOR_POOL)
        self._cursor = 0

    def propose(self, trace: str | None = None, batch_size: int = 2):
        batch = self._pool[self._cursor : self._cursor + batch_size]
        self._cursor += len(batch)
        return batch


def generator_to_candidate(gen, track: str = "B") -> Candidate:
    """Bind a historical increment generator to the arithmetic specification."""
    reference = KnownAlgorithmLibrary().synthesize("increment", 1)
    return reference.implementation(f"invented:{gen.__name__.lstrip('_')}", track, gen)


@dataclass
class ArchiveEntry:
    candidate: Candidate
    report: VerificationReport
    certificate: InnovationCertificate | None = None

    @property
    def width_max(self) -> int:
        return self.report.peak_qubits[self.report.sizes[-1]]


def _profile(rep: VerificationReport) -> tuple[int, ...]:
    metrics = (rep.peak_qubits, rep.toffoli, rep.gate_count, rep.depth)
    return tuple(metric[n] for n in rep.sizes for metric in metrics)


def _quality(entry: ArchiveEntry) -> tuple:
    profile = _profile(entry.report)
    return (*(sum(profile[i::4]) for i in (1, 2, 3, 0)), entry.candidate.id)


def _dominates(a: tuple[int, ...], b: tuple[int, ...]) -> bool:
    return a != b and all(x <= y for x, y in zip(a, b, strict=True))


class MAPelitesArchive:
    """Store one elite per logical width and four-layer depth band."""

    def __init__(self):
        self.niches: dict[tuple[int, int], ArchiveEntry] = {}

    def try_insert(self, cand: Candidate, rep: VerificationReport, certificate: InnovationCertificate | None = None) -> bool:
        if not rep.passed or not rep.sizes:
            return False
        n = rep.sizes[-1]
        key = rep.peak_qubits[n], rep.depth[n] // 4
        entry = ArchiveEntry(cand, rep, certificate)
        current = self.niches.get(key)
        if current is not None and _quality(entry) >= _quality(current):
            return False
        self.niches[key] = entry
        return True

    def best(self) -> ArchiveEntry | None:
        return min(self.niches.values(), key=_quality, default=None)

    def all_passed(self) -> list[ArchiveEntry]:
        return sorted(self.niches.values(), key=_quality)


@dataclass(frozen=True)
class ComparatorProgram:
    fuse: bool = False
    polarity: bool = False


EVOLUTION_FAMILIES = ("majority", "weighted_threshold", "less_than")


class _CircuitEvolution:
    def __init__(self, family: str, reference: Candidate, seed: int):
        self.family = family
        self.reference = reference
        self.rng = random.Random(seed)
        self.programs: dict[str, SumProgram | ComparatorProgram] = {}
        self.lineage: dict[str, dict] = {}
        self.seen: set[SumProgram | ComparatorProgram] = set()
        if family == "less_than":
            self.initial = ComparatorProgram()
            self.choices = {"fuse": (False, True), "polarity": (False, True)}
        else:
            self.initial = SumProgram(fuse=True)
            self.choices = {
                "bounded": (False, True), "fuse": (False, True), "pair": (False, True),
                "marker": ("ripple", "polarity", "overflow"),
            }
            if family == "weighted_threshold":
                self.choices["order"] = ("input", "heavy_first", "low_first", "high_first")

    def _compile(self, n: int, program: SumProgram | ComparatorProgram) -> Circuit:
        if isinstance(program, ComparatorProgram):
            gates = threshold_flag(tuple(range(n - 1)), lessthan_k(n), n - 1, n,
                                   complement=True, fuse=program.fuse, polarity=program.polarity)
            return compact_circuit(n, gates)
        if self.family == "majority":
            return build_sum_threshold((1,) * n, majority_K(n), program)
        return build_sum_threshold(tuple(range(1, n + 1)), weighted_theta(n), program)

    def _candidate(self, program, parent: str | None, mutation: str) -> Candidate:
        suffix = "-".join(str(int(value)) if isinstance(value, bool) else value
                          for value in asdict(program).values())
        candidate_id = f"optimized:{self.family}:{suffix}"
        self.seen.add(program)
        self.programs[candidate_id] = program
        self.lineage[candidate_id] = {"parent": parent, "mutation": mutation, "program": asdict(program)}
        return self.reference.implementation(candidate_id, "B", lambda n: self._compile(n, program))

    def propose(self, archive: MAPelitesArchive, *, evolve: bool, batch_size: int) -> list[Candidate]:
        if not self.seen:
            return [self._candidate(self.initial, None, "initial program")]
        if not evolve:
            return []
        elites = archive.all_passed()
        if not elites:
            return []
        parents = [elites[0], *self.rng.sample(elites[1:], len(elites) - 1)]
        proposals = []
        for entry in parents:
            parent = entry.candidate.id
            current = self.programs[parent]
            mutations = [(field, value) for field, values in self.choices.items() for value in values
                         if value != getattr(current, field)]
            self.rng.shuffle(mutations)
            for field, value in mutations:
                child = replace(current, **{field: value})
                if child in self.seen:
                    continue
                proposals.append(self._candidate(child, parent, f"{field}={value}"))
                if len(proposals) == batch_size:
                    return proposals
                break
        return proposals


@dataclass
class DiscoveryResult:
    certified: list[ArchiveEntry] = field(default_factory=list)
    certificates: dict[str, InnovationCertificate] = field(default_factory=dict)
    attempts: int = 0
    succeeded: bool = False
    trace: list[str] = field(default_factory=list)
    lineage: dict[str, dict] = field(default_factory=dict)
    search_sizes: tuple[int, ...] = ()
    holdout_sizes: tuple[int, ...] = ()

    @property
    def invented(self) -> list[ArchiveEntry]:
        """Compatibility alias for locally certified circuit optimizations."""
        return self.certified


def _frontier(entries: list[ArchiveEntry]) -> list[ArchiveEntry]:
    profiles: dict[tuple[int, ...], ArchiveEntry] = {}
    for entry in sorted(entries, key=_quality):
        profile = tuple(entry.certificate.resources[n][axis]
                        for n in entry.certificate.extended_sizes for axis in RESOURCE_AXES)
        profiles.setdefault(profile, entry)
    return [entry for profile, entry in profiles.items()
            if not any(_dominates(other, profile) for other in profiles)]


def run_discovery(
    mutator: MutatorStandIn | None = None,
    sizes=(3, 4, 5),
    max_batches: int = 16,
    seed: int = 0,
    mode: str = "necessity",
    *,
    problem_key: str = "increment",
    holdout_sizes=None,
    evolve: bool = True,
) -> DiscoveryResult:
    """Evolve and score on search sizes, then certify on untouched sizes."""
    if type(max_batches) is not int or max_batches < 0:
        raise ValueError("max_batches must be a nonnegative integer")
    if mode not in ("necessity", "opportunity"):
        raise ValueError(f"unknown discovery mode: {mode!r}")
    sizes = verification_sizes(sizes)
    if holdout_sizes is None:
        holdout_sizes = tuple(n for n in (6, 7, 8, 9, 10, 11, 12) if n not in sizes)[:3]
    holdout = verification_sizes(holdout_sizes)
    if set(sizes).intersection(holdout):
        raise ValueError("search and holdout sizes must be disjoint")
    result = DiscoveryResult(search_sizes=sizes, holdout_sizes=holdout)
    library = KnownAlgorithmLibrary()
    if not library.has(problem_key):
        result.trace.append(f"No reversible contract registered for {problem_key!r}")
        return result

    reference = library.synthesize(problem_key, sizes[-1])
    specialized = specialized_baselines(library).get(problem_key)
    baselines = [reference]
    if problem_key == "increment":
        baselines.extend(b for b in sota_baselines() if b.id == "sota:cdkm-increment")
    if specialized is not None:
        baselines.append(specialized)
    archive = MAPelitesArchive()
    evolution = None
    legacy = mutator
    if legacy is None:
        if problem_key in EVOLUTION_FAMILIES:
            evolution = _CircuitEvolution(problem_key, reference, seed)
        elif problem_key == "increment":
            legacy = MutatorStandIn(seed)
        else:
            result.trace.append(f"No Track B synthesis grammar for {problem_key!r}")
            return result

    verified = {}
    for _ in range(max_batches):
        if evolution is not None:
            candidates = evolution.propose(archive, evolve=evolve, batch_size=2)
        else:
            generators = legacy.propose("\n".join(result.trace), batch_size=2)[:2]
            candidates = [reference.implementation(f"invented:{gen.__name__.lstrip('_')}", "B", gen)
                          for gen in generators]
        if not candidates:
            break
        for candidate in candidates:
            if candidate.id in verified:
                continue
            report = evaluate_candidate(candidate, sizes=sizes)
            result.attempts += 1
            verified[candidate.id] = ArchiveEntry(candidate, report)
            if report.passed:
                elite = archive.try_insert(candidate, report)
                result.trace.append(f"PASS {candidate.id} {'(elite)' if elite else ''}")
            else:
                result.trace.append(f"FAIL {candidate.id}: {report.failure_reasons[0]}")

    if evolution is not None:
        result.lineage = evolution.lineage
    finalists = [entry for entry in verified.values() if entry.report.passed]
    checked_sizes = verification_sizes((1, 2, *sizes, *holdout))
    accepted = []
    for entry in finalists:
        cert = certify_innovation(entry.candidate, report=entry.report, baseline_candidates=baselines,
                                  extended_sizes=checked_sizes)
        result.certificates[entry.candidate.id] = cert
        if cert.certified:
            entry.certificate = cert
            accepted.append(entry)
        else:
            result.trace.append(f"UNCERTIFIED {entry.candidate.id}: {cert.reasons[0]}")
    result.certified = _frontier(accepted)
    result.succeeded = bool(result.certified)
    return result
