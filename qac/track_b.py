"""Layer 2B: evaluator-guided evolutionary discovery (Track B).

Implements the design's discovery loop at micro scale:
  - a deterministic "LLM-mutator stand-in" that proposes parameterized
    circuit-generator programs (Python functions of n building Circuits);
  - a MAP-Elites archive keyed by (Toffoli-band, depth-band) niches;
  - the Layer-3 semantic sandbox as the only fitness oracle (Maker-Checker);
  - necessity mode (no known algorithm) and opportunity mode (beat a known
    baseline) per AGENT_DESIGN.md section 2.

The flagship invention here is the *lean interleaved incrementer*
(_lean_interleaved_increment): by dropping the library's redundant A_1
ancilla slot and interleaving apply/uncompute top-down (pebble-game qubit
scheduling), it saves one qubit and two gates at identical Toffoli count —
a Pareto dominance over the textbook reference, exactly the trade the
design doc's islands optimize.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .kickmix import CNOT, Circuit, SWAP, Toffoli, X, Z
from .sandbox import evaluate_candidate, VerificationReport
from .library import Candidate, increment_spec, build_increment_ancilla_reference
from .novelty import InnovationCertificate, certify_innovation


# ---------------------------------------------------------------------------
# Mutator: proposes parameterized generator programs (size n is an argument)
# ---------------------------------------------------------------------------

def _lean_interleaved_increment(n: int) -> Circuit:
    """INVENTED: "lean interleaved" incrementer — Pareto-dominates the library.

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
    """INVENTED: "top-write" incrementer — strictly Pareto-dominates the lean
    interleaved incrementer on ALL FOUR axes (width, Toffoli, gates, depth).

    Key idea: the previous best design materializes the top carry
    A_{n-1} = AND(b_0..b_{n-2}) in a slot only to apply it with a CNOT and
    uncompute it immediately — but A_{n-1} is never an input to any higher
    carry, and the top data bit b_{n-1} is never read again after its flip.
    So the top carry is applied IN PLACE as a single gate:
        Toffoli(s_{n-2}, b_{n-2}, b_{n-1})   # b_{n-1} ^= A_{n-1}
    eliminating the top slot AND its apply-CNOT AND its uncompute-Toffoli.
    The rest is the same interleaved prefix-AND schedule, one level shorter.

    Machine-verified n=1..8 (exhaustive semantics + ancilla-clean):
      width   2n-3   vs lean 2n-2  (one fewer qubit),
      toffoli 2n-5   vs lean 2n-4  (one fewer Toffoli),
      gates/depth 3n-6 vs lean 3n-4 (two fewer each).
    Honest scope: the formulas hold for n>=3; at n=1..2 the base cases coincide
    with the lean construction by design, so the strict-dominance claim vs lean
    is scoped to n>=3. Against the gate's actual baselines (known:increment-
    ancilla, sota:cdkm-increment) it is <= on all axes with a strict win on
    every axis at every checked size n=2..6.
    """
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
    """Deterministic stand-in for the frontier-LLM proposer.

    Emits candidate parameterized generators (functions of n) — never static
    circuits — per design section 2.1. A real deployment swaps this for an
    LLM prompted with the feedback trace; the archive/scoring contract stays
    identical.
    """

    def __init__(self, seed: int = 0, pool=None):
        self.rng = random.Random(seed)
        self._pool = list(pool if pool is not None else GENERATOR_POOL)
        self._cursor = 0

    def propose(self, trace: str | None = None, batch_size: int = 2):
        batch = self._pool[self._cursor : self._cursor + batch_size]
        self._cursor += len(batch)
        return batch


def generator_to_candidate(gen, track: str = "B") -> Candidate:
    """Wrap a generator as a Candidate, declaring its ancilla slots.

    The lean interleaved incrementer keeps n-2 prefix-AND slots at qubits
    [n, 2n-2); the rejected mutations are ancilla-free (width n).
    """
    from .library import Candidate as _Cand

    def ancilla_fn(n: int) -> list[int]:
        c = gen(n)
        return list(range(n, c.n))

    return Candidate(
        f"invented:{gen.__name__.lstrip('_')}",
        track,
        gen,
        increment_spec,
        lambda n: n,
        ancilla_fn,
    )


# ---------------------------------------------------------------------------
# MAP-Elites archive with Pareto niches (space x time)
# ---------------------------------------------------------------------------

@dataclass
class ArchiveEntry:
    candidate: Candidate
    report: VerificationReport
    certificate: InnovationCertificate | None = None
    toffoli_max: int = 0
    depth_max: int = 0
    gate_max: int = 0
    width_max: int = 0


def niche_key(rep: VerificationReport) -> tuple[int, int]:
    m = rep.sizes[-1] if rep.sizes else 0
    t = rep.toffoli.get(m, 999)
    d = rep.depth.get(m, 9999)
    return (t // 2, d // 4)


class MAPelitesArchive:
    def __init__(self):
        self.niches: dict[tuple, ArchiveEntry] = {}

    def try_insert(self, cand: Candidate, rep: VerificationReport, certificate: InnovationCertificate | None = None) -> bool:
        if not rep.passed:
            return False
        m = rep.sizes[-1] if rep.sizes else 0
        key = niche_key(rep)
        entry = ArchiveEntry(
            cand, rep, certificate,
            rep.toffoli.get(m, 0), rep.depth.get(m, 0),
            rep.gate_count.get(m, 0), rep.peak_qubits.get(m, 0),
        )
        cur = self.niches.get(key)
        if cur is None or _better(entry, cur):
            self.niches[key] = entry
            return True
        return False

    def best(self) -> ArchiveEntry | None:
        if not self.niches:
            return None
        return min(
            self.niches.values(),
            key=lambda e: (e.width_max, e.toffoli_max, e.gate_max, e.depth_max),
        )

    def all_passed(self) -> list[ArchiveEntry]:
        return list(self.niches.values())


def _better(new: ArchiveEntry, cur: ArchiveEntry) -> bool:
    return (new.width_max, new.toffoli_max, new.gate_max, new.depth_max) < (
        cur.width_max, cur.toffoli_max, cur.gate_max, cur.depth_max,
    )


# ---------------------------------------------------------------------------
# The discovery campaign (budget-boxed)
# ---------------------------------------------------------------------------

@dataclass
class DiscoveryResult:
    invented: list = field(default_factory=list)
    certified: list = field(default_factory=list)  # triple-gated (correct x valuable x novel)
    certificates: dict = field(default_factory=dict)  # candidate id -> InnovationCertificate
    attempts: int = 0
    succeeded: bool = False
    trace: list = field(default_factory=list)


def run_discovery(
    mutator: MutatorStandIn | None = None,
    sizes=(3, 4, 5),
    max_batches: int = 2,
    seed: int = 0,
    mode: str = "necessity",
) -> DiscoveryResult:
    """Budget-boxed Track B campaign.

    necessity mode: success = at least one verified novel candidate.
    opportunity mode: success = a verified candidate that Pareto-dominates
    the library's ancilla-assisted reference (fewer total qubits AND no
    more Toffolis at the largest size).
    """
    mutator = mutator or MutatorStandIn(seed)
    archive = MAPelitesArchive()
    res = DiscoveryResult()
    trace: list[str] = []

    for _ in range(max_batches):
        gens = mutator.propose("\n".join(trace) if trace else None)
        if not gens:
            break
        for gen in gens:
            cand = generator_to_candidate(gen)
            rep = evaluate_candidate(cand, sizes=sizes)
            res.attempts += 1
            if rep.passed:
                cert = certify_innovation(cand, report=rep)
                res.certificates[cand.id] = cert
                if cert.certified:
                    inserted = archive.try_insert(cand, rep, cert)
                    m = rep.sizes[-1]
                    trace.append(
                        f"PASS{' NEW-NICHE' if inserted else ' dominated'} {gen.__name__}: "
                        f"w={rep.peak_qubits.get(m)} t={rep.toffoli.get(m)} "
                        f"g={rep.gate_count.get(m)} d={rep.depth.get(m)} "
                        f"| CERTIFIED correct+valuable+novel"
                    )
                else:
                    failed_gates = [
                        g for g, ok in (
                            ("correct", cert.correct), ("valuable", cert.valuable), ("novel", cert.novel),
                        ) if not ok
                    ]
                    trace.append(
                        f"PASS-SANDBOX BUT UNCERTIFIED {gen.__name__}: "
                        f"fails [{' & '.join(failed_gates)}] — {cert.reasons[:1]}"
                    )
            else:
                trace.append(f"FAIL {gen.__name__}: {rep.failure_reasons[:2]}")

    res.invented = archive.all_passed()
    res.certified = [e for e in res.invented if e.certificate is not None and e.certificate.certified]
    if mode == "necessity":
        res.succeeded = len(res.certified) > 0
    else:
        ref = build_increment_ancilla_reference(sizes[-1])
        ref_profile = (ref.n, ref.toffoli_count())
        res.succeeded = any(
            e.certificate is not None
            and e.certificate.certified
            and (e.width_max, e.toffoli_max) <= ref_profile
            for e in res.invented
        ) and len(res.certified) > 0
    res.trace = trace
    return res
