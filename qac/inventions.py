"""Layer 2A inventions: circuits the machine certifies as correct x valuable x novel.

Every builder here is bound to an existing library contract through
``Candidate.implementation`` so it inherits the exact same specification and
data layout as its textbook counterpart. That makes the comparison apples to
apples: same function, same qubit convention, only the gate schedule differs.

Invention principle used throughout — **top-write**:

    The carry that feeds the most significant bit is chain-terminal: the bit it
    lands on is about to be overwritten anyway. So instead of storing that carry
    in a scratch slot (then applying it with a CNOT, then uncomputing the slot
    with a Toffoli), write it straight into the destination bit with the last
    Toffoli of the chain. This removes one scratch qubit and two gates
    (W -1, T -1, G/D -2) with no change in semantics.

The second principle is **complement-side selection**: for comparisons the
complemented register can be chosen so the flag update needs no extra
inversion, because ``[x < y] = carry_out(~x + y)``.

All circuits use only {X, CNOT, Toffoli} and are verified exhaustively over all
basis inputs at the sizes pinned in ``tests/test_qac.py``.
"""

from __future__ import annotations

from .kickmix import CNOT, Circuit, Toffoli, X
from .library import Candidate, KnownAlgorithmLibrary, popcount_m
from .sota_baselines import _maj, _uma, mod_adder_spec
from .track_b import _topwrite_increment

__all__ = [
    "topwrite_ctrl_increment",
    "topwrite_negate",
    "topwrite_popcount",
    "compare_carry",
    "lean_mod_adder",
    "lean_mod_adder_candidate",
    "inventions",
    "sota_inventions",
]


# ---------------------------------------------------------------------------
# 1. Controlled incrementer: x -> (x + q) mod 2^n with a single control q.
# ---------------------------------------------------------------------------

def topwrite_ctrl_increment(n: int) -> Circuit:
    """Controlled incrementer with dead-value top write.

    Contract (``ctrl_increment_spec``): data bits 0..n-1 hold x, bit n holds the
    control q; the map is x -> (x + q) mod 2^n, q unchanged.

    Textbook (``known:ctrl-increment-ripple``) allocates n-1 carry slots, applies
    the top carry with a CNOT into bit n-1, then uncomputes its slot. Here the
    top carry is written straight into bit n-1, so only n-2 slots exist.

    Cost: W = 2n - 1, T = 2n - 3, G = 3n - 4, D = 3n - 4 (n >= 3).
    """
    if n == 1:
        return Circuit(2, [CNOT(1, 0)])
    q = n
    slot = lambda j: n + j  # carry c_j, j = 1..n-2
    if n == 2:
        return Circuit(3, [Toffoli(0, q, 1), CNOT(q, 0)])
    c = Circuit(2 * n - 1)
    c.gates.append(Toffoli(0, q, slot(1)))  # c_1 = x_0 & q
    for j in range(1, n - 2):  # c_2 .. c_{n-2}
        c.gates.append(Toffoli(j, slot(j), slot(j + 1)))
    c.gates.append(Toffoli(n - 2, slot(n - 2), n - 1))  # TOP-WRITE: x_{n-1} ^= c_{n-1}
    for j in range(n - 2, 1, -1):  # top-down sums + slot uncompute
        c.gates.append(CNOT(slot(j), j))
        c.gates.append(Toffoli(j - 1, slot(j - 1), slot(j)))
    c.gates.append(CNOT(slot(1), 1))
    c.gates.append(Toffoli(0, q, slot(1)))  # uncompute c_1
    c.gates.append(CNOT(q, 0))  # s_0 ^= q LAST (reads the original x_0)
    return c


# ---------------------------------------------------------------------------
# 2. Negation: x -> (-x) mod 2^n, built on the top-write incrementer.
# ---------------------------------------------------------------------------

def topwrite_negate(n: int) -> Circuit:
    """Two's-complement negation x -> (-x) mod 2^n.

    Identity: -x = NOT(x) + 1. The textbook ``known:negate-wrap`` wraps a
    bit-complement around a full incrementer, so it inherits every superfluous
    carry slot. Reusing the top-write incrementer removes them.

    Cost: W = 2n - 3, T = 2n - 5, G = 4n - 6, D = 3n - 3 (n >= 3).
    """
    if n == 1:
        return Circuit(1)  # -x = x mod 2
    inc = _topwrite_increment(n)
    gates = [X(i) for i in range(n)] + list(inc.gates)
    return Circuit(inc.n, gates)


# ---------------------------------------------------------------------------
# 3. Population count: c -> (c + popcount(x)) mod 2^m.
# ---------------------------------------------------------------------------

def topwrite_popcount(n: int) -> Circuit:
    """Hamming-weight counter with dead-value top write.

    Contract (``popcount_spec``): data bits 0..n-1 hold x, the m-bit counter
    (m = ceil(log2(n+1))) sits at bits n..n+m-1, and the map is
    c -> (c + popcount(x)) mod 2^m.

    Textbook (``known:popcount-ripple``) runs a full ripple carry chain per data
    bit, so it needs m-1 carry slots and three gates on the top carry. Here the
    top carry goes straight into the counter's most significant bit: m-2 slots,
    two gates on the top carry.

    Cost: W = n + 2m - 2, T = 2n(m-1) - n, G = 5n + 2n(m-2) ... pinned per size
    in the tests rather than restated as a formula.
    """
    m = popcount_m(n)
    width = n + m + max(m - 2, 0)
    c = Circuit(width)
    base = n  # counter bit c_j at base + j
    g = n + m  # carry slot g_j at g + j - 1
    for i in range(n):
        if m == 1:  # 1-bit counter: a controlled increment is one CNOT
            c.gates.append(CNOT(i, base))
            continue
        if m == 2:  # single carry, written into the dead top counter bit
            c.gates.append(Toffoli(i, base, base + 1))
            c.gates.append(CNOT(i, base))
            continue
        c.gates.append(Toffoli(i, base, g))  # g_1 = x_i & c_0
        for j in range(1, m - 2):  # g_2 .. g_{m-2}
            c.gates.append(Toffoli(g + j - 1, base + j, g + j))
        c.gates.append(Toffoli(g + m - 3, base + m - 2, base + m - 1))  # TOP-WRITE
        for j in range(m - 2, 0, -1):  # top-down sums + slot uncompute
            c.gates.append(CNOT(g + j - 1, base + j))
            if j >= 2:
                c.gates.append(Toffoli(g + j - 2, base + j - 1, g + j - 1))
            else:
                c.gates.append(Toffoli(i, base, g))
        c.gates.append(CNOT(i, base))
    return c


# ---------------------------------------------------------------------------
# 4. Comparator: flag -> flag ^ [x < y] using carry(~x + y).
# ---------------------------------------------------------------------------

def compare_carry(n: int) -> Circuit:
    """Ripple comparator that complements the x register instead of y.

    Contract (``compare_spec``): x = bits 0..n-1, y = bits n..2n-1, flag = bit
    2n, one ancilla = bit 2n+1. Map: flag ^= [x < y].

    Textbook (``known:compare-ripple``) computes carry(x + ~y + 1) and then
    needs X on every y bit, an X on the carry ancilla, a CNOT into the flag and
    two more X gates on the flag. Choosing the complement side by the identity

        [x < y]  <=>  carry_out(~x + y)      (carry-in 0)

    removes all five of those inversions while keeping the same carry chain.
    Width, Toffoli count and depth are unchanged; gate count drops by 3 per size.

    Verified at n = 1..6: at n = 7 the dense-verification width cap of the
    sandbox is reached (2n + 2 = 16), which is an evaluator limit, not a defect.
    """
    flag = 2 * n
    anc = flag + 1  # carry-in slot, stays |0>
    c = Circuit(2 * n + 2)
    maj: list = []
    for i in range(n):
        carry_in = anc if i == 0 else (n + i - 1)
        maj += [CNOT(n + i, i), CNOT(n + i, carry_in), Toffoli(carry_in, i, n + i)]
    c.gates += [X(i) for i in range(n)]  # x <- ~x
    c.gates += maj  # y_{n-1} holds carry_out(~x + y) = [x >= y]
    c.gates.append(CNOT(2 * n - 1, flag))  # X(flag) cancels against the flag's own value
    c.gates += list(reversed(maj))  # restore the chain
    c.gates += [X(i) for i in range(n)]  # restore x
    return c


# ---------------------------------------------------------------------------
# 5. Modular adder: (a, b) -> (a, a + b mod 2^n) on the CDKM contract.
# ---------------------------------------------------------------------------

def lean_mod_adder(n: int) -> Circuit:
    """CDKM in-place modular adder with both provable trims applied.

    Contract (``mod_adder_spec``): data bits 0..n-1 hold a, bits n..2n-1 hold b;
    map (a, b) -> (a, a + b mod 2^n). Same layout as ``sota:cdkm-mod-adder``
    (a[i] = i, b[i] = n + i, helper = 2n), so the comparison is apples to apples.

    Two savings that leave semantics untouched:

    1. **Zero-helper tail.** The final UMA acts on (a_0, b_0, h), and by the time
       its last gate runs the helper has already been returned to |0| by the
       MAJ/UMA pair. The closing ``CNOT(z, y)`` therefore flips a qubit that is
       provably 0 — deleting it is exact, not approximate. Saves one CNOT per
       size at every n.

    2. **Helper-free n = 1.** One-bit modular addition is ``b_0 ^= a_0``: a single
       CNOT, no carry at all. Width 2, Toffoli 0, gates 1 instead of 3/2/6.

    Cost (n >= 2): W = 2n + 1, T = 2n, G = 6n - 1, D = 5n. Dominates the CDKM
    baseline strictly on width (n = 1), gates and depth at every size.
    """
    if n == 1:
        return Circuit(2, [CNOT(0, 1)])
    a = lambda i: i
    b = lambda i: n + i
    h = 2 * n
    gates: list = []
    gates += _maj(a(0), b(0), h)
    for i in range(n - 1):
        gates += _maj(a(i + 1), b(i + 1), a(i))
    for i in reversed(range(n - 1)):
        gates += _uma(a(i + 1), b(i + 1), a(i))
    gates += _uma(a(0), b(0), h)[:-1]  # drop the provably no-op final CNOT
    return Circuit(2 * n + 1, gates)


def lean_mod_adder_candidate() -> Candidate:
    """Standalone candidate against ``mod_adder_spec`` (no library family exists)."""
    return Candidate(
        "invented:modadd-lean",
        "A",
        lean_mod_adder,
        mod_adder_spec,
        lambda n: 2 * n,
        lambda n: [2 * n] if n > 1 else [],
    )


# ---------------------------------------------------------------------------
# Registry: problem key -> (candidate_id, builder, pinned sizes for verification)
# ---------------------------------------------------------------------------

def _bind(problem_key: str, candidate_id: str, builder):
    family = KnownAlgorithmLibrary().synthesize(problem_key, 1)
    return family.implementation(candidate_id, "B", builder)


def inventions() -> dict[str, list[tuple[Candidate, tuple[int, ...]]]]:
    """Certifiable inventions, grouped by the library contract they implement.

    The second element is the size range on which the sandbox can verify them
    exhaustively.
    """
    return {
        "ctrl_increment": [
            (_bind("ctrl_increment", "invented:topwrite-ctrl-increment", topwrite_ctrl_increment),
             (1, 2, 3, 4, 5, 6, 7, 8)),
        ],
        "negate": [
            (_bind("negate", "invented:topwrite-negate", topwrite_negate),
             (1, 2, 3, 4, 5, 6, 7, 8)),
        ],
        "popcount": [
            (_bind("popcount", "invented:topwrite-popcount", topwrite_popcount),
             (1, 2, 3, 4, 5, 6, 7, 8)),
        ],
        "compare": [
            (_bind("compare", "invented:compare-carry", compare_carry),
             (1, 2, 3, 4, 5, 6)),
        ],
    }


def sota_inventions() -> list[tuple[Candidate, tuple[int, ...]]]:
    """Inventions whose only same-contract baseline lives in ``sota_baselines``.

    There is no ``mod_adder`` family in the known-algorithm library, so these
    are compared against the CDKM baseline directly (the certificate picks it up
    through ``sota_baselines()``).
    """
    return [
        (lean_mod_adder_candidate(), (1, 2, 3, 4, 5, 6)),
    ]
