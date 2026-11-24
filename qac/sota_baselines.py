"""Cited and problem-specialized comparison implementations.

Source (single citation for this module):
  Cuccaro, Draper, Kutin, Moulton, "A new quantum ripple-carry addition
  circuit", arXiv:quant-ph/0410184 (2004) — the CDKM adder.
  Transcribed literally from Qiskit's adder_ripple_c04
  (qiskit/synthesis/arithmetic/adders/cdkm_ripple_carry_adder.py,
  kind="fixed"), which is a direct transcription of the paper's Fig. 1/2:

      MAJ(x,y,z) = CX(x,y), CX(x,z), CCX(z,y,x)
      UMA(x,y,z) = CCX(z,y,x), CX(x,z), CX(z,y)

The CDKM increment and constant-add entries instantiate a general adder
with a prepared constant register. The shared-prefix reference below
specializes the circuit to the increment problem itself.
"""

from __future__ import annotations

import hashlib

from .arithmetic import SumProgram, build_sum_threshold, compact_circuit, controlled_increment
from .kickmix import CNOT, Circuit, Toffoli, X
from .library import Candidate, KnownAlgorithmLibrary, addconst_k, addconst_spec, decrement_spec, increment_spec
from .library import majority_K, weighted_theta

CUCCARO_CITATION = (
    "Cuccaro, Draper, Kutin, Moulton: A new quantum ripple-carry addition "
    "circuit, arXiv:quant-ph/0410184 (2004); transcribed from Qiskit "
    "adder_ripple_c04 (kind=fixed); "
    "MAJ(x,y,z)=CX(x,y),CX(x,z),CCX(z,y,x); "
    "UMA(x,y,z)=CCX(z,y,x),CX(x,z),CX(z,y)"
)

CITATION_SHA256 = hashlib.sha256(CUCCARO_CITATION.encode()).hexdigest()


def _maj(x: int, y: int, z: int) -> list:
    """CDKM majority gate (paper Fig. 1); in-place on (x, y, z)."""
    return [CNOT(x, y), CNOT(x, z), Toffoli(z, y, x)]


def _uma(x: int, y: int, z: int) -> list:
    """CDKM 'UnMajority and Add' gate (two CNOTs and one Toffoli)."""
    return [Toffoli(z, y, x), CNOT(x, z), CNOT(z, y)]


def build_cuccaro_mod_adder(n: int) -> Circuit:
    """CDKM 'fixed' mod-adder: (a, b) -> (a, a + b mod 2^n).

    Layout: a[i] = i, b[i] = n + i, helper = 2n (starts/ends |0>).
    Data width 2n, one helper ancilla, total width 2n + 1.

    Measured cost (n>=1): Toffoli 2n, gates 6n, depth 5n + 1.
    """
    a = lambda i: i
    b = lambda i: n + i
    h = 2 * n
    gates: list = []
    gates += _maj(a(0), b(0), h)
    for i in range(n - 1):
        gates += _maj(a(i + 1), b(i + 1), a(i))
    for i in reversed(range(n - 1)):
        gates += _uma(a(i + 1), b(i + 1), a(i))
    gates += _uma(a(0), b(0), h)
    return Circuit(2 * n + 1, gates)


def build_cuccaro_increment(n: int) -> Circuit:
    """Incrementer as the CDKM adder with constant addend a = 1.

    Layout: data b[i] = i, addend register
    a[i] = n + i (X-set to 1, then uncomputed), helper = 2n.
    Data width n, ancillas n + 1, total width 2n + 1.

    Measured cost (n>=2): Toffoli 2n, gates 6n + 2, depth 5n + 2.
    """
    a = lambda i: n + i
    b = lambda i: i
    h = 2 * n
    gates: list = [X(a(0))]  # a := 1
    gates += _maj(a(0), b(0), h)
    for i in range(n - 1):
        gates += _maj(a(i + 1), b(i + 1), a(i))
    for i in reversed(range(n - 1)):
        gates += _uma(a(i + 1), b(i + 1), a(i))
    gates += _uma(a(0), b(0), h)
    gates.append(X(a(0)))  # uncompute a := 1
    return Circuit(2 * n + 1, gates)


def build_cuccaro_addconst(n: int, k: int | None = None) -> Circuit:
    """Constant adder as the CDKM adder with a := k: x -> x + k mod 2^n.

    Defaults to the library's size-derived constant k = addconst_k(n) = n,
    making this baseline same-spec with known:add_const. Layout: data
    b[i] = i, addend register a[i] = n + i (X-set to k, then uncomputed),
    helper = 2n. Total width 2n + 1.

    Measured cost (n>=2): Toffoli 2n, gates 6n + 2*popcount(k).
    """
    k = addconst_k(n) if k is None else k
    a = lambda i: n + i
    b = lambda i: i
    h = 2 * n
    gates: list = [X(a(i)) for i in range(n) if (k >> i) & 1]
    gates += _maj(a(0), b(0), h)
    for i in range(n - 1):
        gates += _maj(a(i + 1), b(i + 1), a(i))
    for i in reversed(range(n - 1)):
        gates += _uma(a(i + 1), b(i + 1), a(i))
    gates += _uma(a(0), b(0), h)
    gates += [X(a(i)) for i in range(n) if (k >> i) & 1]
    return Circuit(2 * n + 1, gates)


def mod_adder_spec(n: int) -> dict[int, int]:
    """Ground truth: (a, b) -> (a, a + b mod 2^n) over 2n data bits
    (a in the low n bits, b in the high n bits, little-endian)."""
    mask = (1 << n) - 1
    return {
        x: (x & mask) + ((((x & mask) + (x >> n)) & mask) << n)
        for x in range(1 << (2 * n))
    }


# ---------------------------------------------------------------------------
# Candidate surface (sandbox contract) + registry
# ---------------------------------------------------------------------------

def cuccaro_mod_adder_candidate() -> Candidate:
    return Candidate(
        "sota:cdkm-mod-adder",
        "A",
        build_cuccaro_mod_adder,
        mod_adder_spec,
        lambda n: 2 * n,
        lambda n: [2 * n],
    )


def cuccaro_increment_candidate() -> Candidate:
    return Candidate(
        "sota:cdkm-increment",
        "A",
        build_cuccaro_increment,
        increment_spec,
        lambda n: n,
        lambda n: list(range(n, 2 * n + 1)),
    )


def cuccaro_addconst_candidate() -> Candidate:
    return Candidate(
        "sota:cdkm-addconst",
        "A",
        build_cuccaro_addconst,
        addconst_spec,
        lambda n: n,
        lambda n: list(range(n, 2 * n + 1)),
    )


def sota_baselines() -> list[Candidate]:
    """Retain the original CDKM benchmark IDs for reproducible comparisons."""
    return [
        cuccaro_mod_adder_candidate(),
        cuccaro_increment_candidate(),
        cuccaro_addconst_candidate(),
    ]


def build_shared_prefix_increment(n: int) -> Circuit:
    """The descending controlled-X ladder with shared clean AND prefixes.

    A bit flips exactly when all lower bits are one. Write the longest
    conjunction directly into the high bit, then apply and erase each
    shorter AND prefix while the lower data bits are still unmodified.
    """
    gates = controlled_increment(0, tuple(range(1, n)), n, fuse=True)
    return compact_circuit(n, gates + [X(0)])


def build_shared_prefix_decrement(n: int) -> Circuit:
    """Shared-prefix decrementer: the gate-reversal dual of the increment ladder.

    Decrement is the time-reverse of increment (both are their own inverse
    functions only at the gate level: D = I^{-1}, and circuit reversal IS
    inverse for a gate set closed under self-inverse gates). Reversing the
    shared-prefix ladder therefore yields a decrementer at the incrementer's
    exact cost. This is the honest same-spec reference: any decrementer built
    by reversing an incrementer must compare against it, not against the
    textbook wrap reference (which pays a full padded register).
    """
    inc = build_shared_prefix_increment(n)
    return Circuit(inc.n, list(reversed(inc.gates)))


def specialized_baselines(library: KnownAlgorithmLibrary) -> dict[str, Candidate]:
    """Same-contract references with the main textbook inefficiencies removed."""
    fixed_width = SumProgram(fuse=True)

    def fixed_width_majority(n: int) -> Circuit:
        return build_sum_threshold((1,) * n, majority_K(n), fixed_width)

    def fixed_width_weighted(n: int) -> Circuit:
        return build_sum_threshold(tuple(range(1, n + 1)), weighted_theta(n), fixed_width)

    return {
        "increment": library.synthesize("increment", 1).implementation(
            "reference:shared-prefix-increment", "A", build_shared_prefix_increment),
        "majority": library.synthesize("majority", 1).implementation(
            "reference:fixed-width-majority", "A", fixed_width_majority),
        "weighted_threshold": library.synthesize("weighted_threshold", 1).implementation(
            "reference:fixed-width-weighted", "A", fixed_width_weighted),
        "decrement": library.synthesize("decrement", 1).implementation(
            "reference:shared-prefix-decrement", "A", build_shared_prefix_decrement),
    }
