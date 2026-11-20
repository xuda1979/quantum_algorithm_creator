"""Layer 2A: known-algorithm library + Track A synthesis.

Stands in for Quantum GraphRAG over the Quantum Algorithm Zoo. Ships
verified, size-parameterized reference generators (the "Bloq library").

Bit convention: qubit i holds bit i of integer x (little-endian).
Increment semantics: x -> (x + 1) mod 2^n, i.e. bit i flips iff all lower
bits are 1.
"""

from __future__ import annotations

from .kickmix import CNOT, Circuit, SWAP, Toffoli, X, Z
from .sandbox import evaluate_candidate, VerificationReport


class Candidate:
    """Uniform candidate surface for both tracks (sandbox contract)."""

    def __init__(self, id: str, track: str, build_fn, spec_fn, n_data_fn, ancilla_fn=lambda n: []):
        self.id = id
        self.track = track
        self._build = build_fn
        self._spec = spec_fn
        self._n_data = n_data_fn
        self._ancilla = ancilla_fn

    def build(self, n):
        return self._build(n)

    def spec(self, n):
        return self._spec(n)

    def n_data(self, n):
        return self._n_data(n)

    def ancilla(self, n):
        return self._ancilla(n)


# ---------------------------------------------------------------------------
# Known incrementer constructions
# ---------------------------------------------------------------------------

def build_increment_ancilla_reference(n: int) -> Circuit:
    """Textbook ancilla-assisted incrementer (the "library standard").

    n data qubits (0..n-1) + n-1 ancilla qubits (n..2n-2); ancilla slot for
    A_i = AND(b_0..b_{i-1}) is qubit n+i-1. Interleaved top-down
    apply/uncompute; X(0) strictly last so uncomputes read original b_0.

    Toffoli cost 2n-4 (n>=2), width 2n-1. Verified exhaustively n<=6.
    """
    if n == 1:
        return Circuit(1, [X(0)])
    total = 2 * n - 1
    c = Circuit(total)

    def anc(i: int) -> int:
        return n + i - 1

    c.gates.append(CNOT(0, anc(1)))  # A_1 = b_0
    for i in range(2, n):
        c.gates.append(Toffoli(anc(i - 1), i - 1, anc(i)))
    for i in range(n - 1, 1, -1):  # top-down: apply then uncompute
        c.gates.append(CNOT(anc(i), i))
        c.gates.append(Toffoli(anc(i - 1), i - 1, anc(i)))
    c.gates.append(CNOT(anc(1), 1))  # b_1 ^= A_1
    c.gates.append(CNOT(0, anc(1)))  # uncompute A_1
    c.gates.append(X(0))  # b_0 ^= 1 LAST
    return c


def increment_spec(n: int) -> dict[int, int]:
    """Ground truth truth table: x -> (x+1) mod 2^n on the data bits."""
    return {x: (x + 1) % (1 << n) for x in range(1 << n)}


def increment_candidate_ancilla() -> Candidate:
    return Candidate(
        "known:increment-ancilla",
        "A",
        build_increment_ancilla_reference,
        increment_spec,
        lambda n: n,
        lambda n: list(range(n, 2 * n - 1)) if n > 1 else [],
    )


def build_bit_reversal(n: int) -> Circuit:
    """Bit-reversal permutation network (endianness-fix subroutine)."""
    c = Circuit(n)
    for i in range(n // 2):
        c.gates.append(SWAP(i, n - 1 - i))
    return c


def bit_reversal_spec(n: int) -> dict[int, int]:
    return {x: int(f"{x:0{n}b}"[::-1], 2) for x in range(1 << n)}


def bit_reversal_candidate() -> Candidate:
    return Candidate(
        "known:bit-reversal-swap",
        "A",
        build_bit_reversal,
        bit_reversal_spec,
        lambda n: n,
        lambda n: [],
    )


class KnownAlgorithmLibrary:
    """Micro GraphRAG: problem key -> verified parameterized generators."""

    def __init__(self):
        self._entries = {
            "increment": increment_candidate_ancilla(),
            "bit_reversal": bit_reversal_candidate(),
        }

    def synthesize(self, problem_key: str, n: int) -> Candidate:
        if problem_key not in self._entries:
            raise KeyError(f"no known algorithm for '{problem_key}'")
        return self._entries[problem_key]

    def has(self, problem_key: str) -> bool:
        return problem_key in self._entries

    def verify_all(self, sizes=(3, 4, 5)) -> dict[str, VerificationReport]:
        return {k: evaluate_candidate(c, sizes=sizes) for k, c in self._entries.items()}
