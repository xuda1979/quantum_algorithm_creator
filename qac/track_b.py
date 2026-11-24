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


def _topwrite_addconst(n: int) -> Circuit:
    """INVENTED: "top-write" constant adder — x -> (x + n) mod 2^n.

    Second algorithm family (constant addition, not just +1). Three savings the
    textbook ripple-carry adder (known:addconst-carry) and CDKM (sota:cdkm-
    addconst) both leave on the table:

      1. Trivial-carry elimination. Carries below the lowest set bit j0 of the
         constant k are identically 0; at j0 the carry out is just c_{j0+1} =
         x_{j0} (a single CNOT). The baselines still run majorities there.
      2. Top-write dead-value fusion. The carry into bit n-1 is chain-terminal
         and its target bit is a dead value, so it is written IN PLACE:
             AND-case: Toffoli(n-2, q, n-1)
             OR-case:  CNOT(n-2,n-1), CNOT(q,n-1), Toffoli(n-2,q,n-1)
         eliminating the top ancilla AND its apply-CNOT AND its uncompute.
      3. Dense ancilla packing. Only the carries c_{j0+1}..c_{n-2} are
         materialized, at qubits q(j) = n + (j - j0 - 1).

    Constant k = addconst_k(n) = n, so for n >= 3 the top constant bit k_{n-1}
    is 0 (n < 2^{n-1}) and the top bit needs only the carry.

    Machine-verified n=1..8 (exhaustive semantics + ancilla-clean, amplitude +1)
    in /tmp/addconst_check.py, independently re-checked with Qiskit
    (/tmp/qiskit_verify.py). Scope: base cases n=1..4 are pinned; the general
    j0-based schedule runs n >= 5. Dominance vs both same-spec baselines at
    every checked size n=2..6 (<= on all axes, strict on >= 1).
    """
    if n == 1:  # +1 mod 2 is NOT x -> flip the only bit
        return Circuit(1, [X(0)])
    if n == 2:  # +2 mod 4 is a plain flip of bit 1
        return Circuit(2, [X(1)])
    if n == 3:  # k = 3 = 0b11: j0 = 0, single materialized carry c_1 = x_0
        return Circuit(
            4,
            [
                CNOT(0, 3),  # c_1 = x_0
                CNOT(1, 2),  # c_2 = x_1 | c_1 written into dead bit 2
                CNOT(3, 2),
                Toffoli(1, 3, 2),
                CNOT(3, 1),  # s_1 ^= c_1
                X(1),  # k_1 = 1
                CNOT(0, 3),  # uncompute c_1
                X(0),  # s_0 = NOT x_0  (k_0 = 1, c_0 = 0) LAST
            ],
        )
    if n == 4:  # k = 4 = 0b100: j0 = 2 = n-2, no ancilla; c_3 = x_2
        return Circuit(4, [CNOT(2, 3), X(2)])

    from .library import addconst_k

    k = addconst_k(n)  # == n
    kb = [(k >> j) & 1 for j in range(n)]
    j0 = next(j for j in range(n) if kb[j])  # lowest set bit of k
    low, top = j0 + 1, n - 2  # materialized carry indices c_low .. c_top
    width = 2 * n - j0 - 2
    c = Circuit(width)
    q = lambda j: n + (j - j0 - 1)  # ancilla slot for carry c_j

    c.gates.append(CNOT(j0, q(low)))  # (1) c_{j0+1} = x_{j0}
    for j in range(low, top):  # (2) c_{j0+2} .. c_{n-2}
        if kb[j]:  # OR:  c_{j+1} = x_j | c_j
            c.gates += [CNOT(j, q(j + 1)), CNOT(q(j), q(j + 1)), Toffoli(j, q(j), q(j + 1))]
        else:  # AND: c_{j+1} = x_j & c_j
            c.gates.append(Toffoli(j, q(j), q(j + 1)))
    if kb[top]:  # (3) top-write c_{n-1} into dead bit n-1 (OR case)
        c.gates += [CNOT(top, n - 1), CNOT(q(top), n - 1), Toffoli(top, q(top), n - 1)]
    else:
        c.gates.append(Toffoli(top, q(top), n - 1))
    if kb[n - 1]:
        c.gates.append(X(n - 1))

    for j in range(top, low - 1, -1):  # (4) apply sums, uncompute top-down
        c.gates.append(CNOT(q(j), j))  # s_j ^= c_j
        if kb[j]:
            c.gates.append(X(j))
        if j == low:  # uncompute c_{j0+1} = x_{j0}
            c.gates.append(CNOT(j0, q(low)))
        elif kb[j - 1]:  # uncompute c_j (OR, self-inverse while inputs unchanged)
            c.gates += [CNOT(j - 1, q(j)), CNOT(q(j - 1), q(j)), Toffoli(j - 1, q(j - 1), q(j))]
        else:
            c.gates.append(Toffoli(j - 1, q(j - 1), q(j)))
    c.gates.append(X(j0))  # (5) s_{j0} = NOT x_{j0} (k_{j0}=1, c_{j0}=0) LAST
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


def _reverse_increment(n: int) -> Circuit:
    """AUDITED DUAL: decrementer obtained by reversing the top-write incrementer.

    Every gate in {X, CNOT, Toffoli} is self-inverse, so reversing the gate
    sequence implements the inverse map: D = I^{-1}, i.e. x -> x-1 mod 2^n.
    Correctness is therefore free, and the cost equals the incrementer's
    (borrow propagation is exactly carry propagation read backwards).

    Honest status: this is NOT a new algorithm. The reversed shared-prefix
    ladder computes the identical unitary at identical cost, so once
    ``reference:shared-prefix-decrement`` is in the same-spec baseline pool the
    machine check reports ``duplicate_of``. Keep it as an executable audit:
    it pins the dual identity and demonstrates the triple gate rejecting a
    correct-but-unoriginal proposal.
    """
    inc = _topwrite_increment(n)
    return Circuit(inc.n, list(reversed(inc.gates)))


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


def generator_to_candidate(gen, track: str = "B", spec=None) -> Candidate:
    """Bind a generator program to a problem specification.

    With no ``spec`` the generator is bound to the library increment contract
    (the family the discovery campaign searches). Passing ``spec`` (for
    example ``addconst_spec``) binds it to another family; the data always
    lives in ``[0, n)`` and every qubit above it is declared scratch.
    """
    if spec is None:
        reference = KnownAlgorithmLibrary().synthesize("increment", 1)
        return reference.implementation(f"invented:{gen.__name__.lstrip('_')}", track, gen)

    def ancilla_fn(n: int) -> list[int]:
        return list(range(n, gen(n).n))

    return Candidate(
        f"invented:{gen.__name__.lstrip('_')}",
        track,
        gen,
        spec,
        lambda n: n,
        ancilla_fn,
    )

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
