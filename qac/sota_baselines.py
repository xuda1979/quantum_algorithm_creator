"""Literature SOTA baselines: the state of the art that any Track-B
"innovation" must actually beat before the certificate may say "valuable".

Source (single citation for this module):
  Cuccaro, Draper, Kutin, Moulton, "A new quantum ripple-carry addition
  circuit", arXiv:quant-ph/0410184 (2004) — the CDKM adder.
  Transcribed literally from Qiskit's adder_ripple_c04
  (qiskit/synthesis/arithmetic/adders/cdkm_ripple_carry_adder.py,
  kind="fixed"), which is a direct transcription of the paper's Fig. 1/2:

      MAJ(x,y,z) = CX(x,y), CX(x,z), CCX(z,y,x)
      UMA(x,y,z) = CCX(z,y,x), CX(x,z), CX(z,y)

Every construction below is exhaustively machine-verified by the sandbox
(check_semantics, all 2^data_n inputs, ancilla-clean, amplitude +1) at
n = 1..6 — see tests/test_qac.py::test_sota_baselines_verified.

Citation SHA-256 (canonical string in CUCCARO_CITATION):
  4a84cc18822acd28fd0b4a86c9ea656a3cfea0ef6442a7546c97b3e95efe4d5f

Honest scope note (paper section 5): the CDKM adder does NOT simplify
when one addend is a classical constant — a constant addend still needs
a quantum register. The incrementer / const-adder baselines therefore
pay the full adder price (width 2n+1, Toffoli 2n), which is what makes
them an honest literature floor rather than a strawman.
"""

from __future__ import annotations

import hashlib

from .kickmix import CNOT, Circuit, Toffoli, X
from .library import Candidate, addconst_k, addconst_spec, increment_spec

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
    """CDKM 'UnMajority and Add' gate (paper Fig. 2, 3-CNOT version)."""
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

    Paper section 5: no simplification for constant addends — the full
    adder price is paid. Layout: data b[i] = i, addend register
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
    """All literature SOTA baselines (the honest 'valuable' floor)."""
    return [
        cuccaro_mod_adder_candidate(),
        cuccaro_increment_candidate(),
        cuccaro_addconst_candidate(),
    ]
