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

from .arithmetic import threshold_flag
from .kickmix import CNOT, Circuit, Toffoli, X
from .library import (Candidate, KnownAlgorithmLibrary, lessthan_k, lessthan_spec,
                      majority_K, majority_spec, popcount_m, weighted_t,
                      weighted_theta, weighted_threshold_spec)
from .sota_baselines import _maj, _uma, mod_adder_spec
from .track_b import _topwrite_increment

__all__ = [
    "topwrite_ctrl_increment",
    "topwrite_negate",
    "topwrite_popcount",
    "compare_carry",
    "compare_topcarry",
    "lessthan_fused",
    "lessthan_fused_candidate",
    "lean_mod_adder",
    "lean_mod_adder_candidate",
    "majority_pruned",
    "majority_pruned_candidate",
    "weighted_pruned",
    "weighted_pruned_candidate",
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
# 5. Constant comparator: f -> f ^ [x < k] with the flag fused into the chain.
# ---------------------------------------------------------------------------

def lessthan_fused(n: int) -> Circuit:
    """Ripple-borrow comparator whose top borrow step writes straight into the flag.

    Contract (``lessthan_spec``): x = bits 0..n-2, flag f = bit n-1, and the map
    is f ^= [x < k] with the fixed threshold k = min(n+1, 2^(n-1) - 1).

    The textbook ``known:lessthan-borrow`` keeps the no-borrow chain
    D_j = [x_{<j} >= k_{<j}] in ancilla slots, materializes D_m in a slot, flips
    the flag with ``CNOT(slot, f)`` + ``X(f)``, then uncomputes the whole chain
    by reversal. Here the *last* borrow step is fused into the flag instead:

    * ``k_{m-1} = 1``: ``f ^= ~(x_{m-1} & D)`` = ``CCX(x_{m-1}, D, f)``, then ``X(f)``
    * ``k_{m-1} = 0``: ``f ^= ~(x_{m-1} | D)`` = ``CNOT(D, f)``, ``CNOT(x_{m-1}, f)``,
      ``CCX(x_{m-1}, D, f)``, then ``X(f)``

    so the flag plays the part of the top slot and no materialize/uncompute
    Toffoli pair is needed for it (W -1, T -1, G/D -2 at every size). The chain
    below the top is constant-folded as well: while the running borrow value is
    the constant one, ``k_j = 0`` steps skip, and ``k_j = 1`` steps reuse a live
    data bit instead of a fresh slot (``D & x_j`` with ``D = 1`` is ``x_j``).

    Verified exhaustively on all 2^n inputs for n = 1..9; costs are pinned per
    size in ``tests/test_qac.py`` (the threshold is irregular, so there is no
    single closed formula).
    """
    m = n - 1
    k = lessthan_k(n)
    if m < 1 or k == 0:
        return Circuit(n)
    flag = n - 1
    if m == 1:  # k = 1: [x < 1] = ~x_0
        return Circuit(2, [CNOT(0, flag), X(flag)])
    kb = [(k >> j) & 1 for j in range(m)]
    cur = ("q", 0) if kb[0] else ("c1",)  # ("c1",) constant one, else (tag, index)
    nxt = n
    compute: list = []
    for j in range(1, m - 1):
        if kb[j]:  # D' = D & x_j
            if cur == ("c1",):
                cur = ("q", j)
            else:
                s = nxt
                nxt += 1
                compute.append(Toffoli(j, cur[1], s))
                cur = ("s", s)
        else:  # D' = D | x_j, written into a fresh zeroed slot
            if cur == ("c1",):
                continue
            s = nxt
            nxt += 1
            compute += [CNOT(j, s), CNOT(cur[1], s), Toffoli(j, cur[1], s)]
            cur = ("s", s)
    c = Circuit(nxt)
    top = m - 1
    if kb[top]:  # f ^= ~(x_top & D) = 1 ^ x_top & D
        tail = ([CNOT(top, flag)] if cur == ("c1",) else [Toffoli(top, cur[1], flag)]) + [X(flag)]
    elif cur == ("c1",):  # D_m = 1, so [x < k] is constant 0 here
        tail = []
    else:  # f ^= ~(x_top | D)
        # Order the two flag CNOTs top-first so the data-bit control (top) is not
        # blocked behind the long chain slot; depth drops by 1..3 with the
        # identical gate count.
        tail = [CNOT(top, flag), CNOT(cur[1], flag), Toffoli(cur[1], top, flag), X(flag)]
    c.gates = compute + tail + list(reversed(compute))
    return c


def lessthan_fused_candidate() -> Candidate:
    return Candidate(
        "invented:lessthan-fused",
        "A",
        lessthan_fused,
        lessthan_spec,
        lambda n: n,
        lambda n: list(range(n, lessthan_fused(n).n)),
    )


# ---------------------------------------------------------------------------
# 6. Modular adder: (a, b) -> (a, a + b mod 2^n) on the CDKM contract.
# ---------------------------------------------------------------------------

def lean_mod_adder(n: int) -> Circuit:
    """CDKM in-place modular adder with three provable trims applied.

    Contract (``mod_adder_spec``): data bits 0..n-1 hold a, bits n..2n-1 hold b;
    map (a, b) -> (a, a + b mod 2^n). Same layout as ``sota:cdkm-mod-adder``
    (a[i] = i, b[i] = n + i, helper = 2n), so the comparison is apples to apples.

    Three savings that leave semantics untouched:

    1. **Folded turning point.** The MAJ chain ends with
       ``MAJ(a_{n-1}, b_{n-1}, a_{n-2})`` and the UMA chain starts with
       ``UMA(a_{n-1}, b_{n-1}, a_{n-2})`` — the *same* triple. MAJ's last gate
       ``CCX(a_{n-2}, b_{n-1}, a_{n-1})`` is literally UMA's first gate, and the
       two inner ``CNOT(a_{n-1}, a_{n-2})`` match too, so the pair collapses to
       ``CNOT(a_{n-1}, b_{n-1})`` followed by ``CNOT(a_{n-2}, b_{n-1})``. Saves
       one Toffoli and two CNOTs (T -1, G -2) at every n >= 2.

    2. **Zero-helper tail.** The final UMA acts on (a_0, b_0, h), and by the time
       its last gate runs the helper has already been returned to |0| by the
       MAJ/UMA pair. The closing ``CNOT(z, y)`` therefore flips a qubit that is
       provably 0 — deleting it is exact, not approximate. Saves one CNOT per
       size at every n.

    3. **Helper-free n = 1.** One-bit modular addition is ``b_0 ^= a_0``: a single
       CNOT, no carry at all. Width 2, Toffoli 0, gates 1 instead of 3/2/6.

    Cost (n >= 2): W = 2n + 1, T = 2n - 2, G = 6n - 5, D = 5n - 4. Dominates the
    CDKM baseline strictly on width (n = 1), and on Toffoli, gates and depth at
    every size.
    """
    if n == 1:
        return Circuit(2, [CNOT(0, 1)])
    a = lambda i: i
    b = lambda i: n + i
    h = 2 * n
    gates: list = []
    gates += _maj(a(0), b(0), h)
    for i in range(n - 2):                        # MAJ chain, stopping below the top
        gates += _maj(a(i + 1), b(i + 1), a(i))
    gates.append(CNOT(a(n - 1), b(n - 1)))        # folded turning point
    gates.append(CNOT(a(n - 2), b(n - 1)))
    for i in reversed(range(n - 2)):              # UMA chain below the top
        gates += _uma(a(i + 1), b(i + 1), a(i))
    gates += _uma(a(0), b(0), h)[:-1]             # drop the provably no-op final CNOT
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
# 7. Comparator: flag-directed top carry — the final carry is never a wire.
# ---------------------------------------------------------------------------

def compare_topcarry(n: int) -> Circuit:
    """Comparator whose most significant carry is folded straight into the flag.

    Contract (``compare_spec``): x = bits 0..n-1, y = bits n..2n-1, flag = bit
    2n, one ancilla = bit 2n+1. Map: flag ^= [x < y].

    Same carry chain as ``invented:compare-carry`` (complement x, then run
    ``carry(~x + y)``), but the chain stops one step short. The last carry
    ``c_n`` is the one nobody needs *stored*: only its parity reaches the flag.
    Using the mux identity

        maj(c, x', y) = x' ^ (x' ^ y)(x' ^ c)

    the top step can therefore consume ``c_{n-1}`` in place and flip the flag
    with a single Toffoli plus two CNOT fix-ups, on wires the chain already
    owns. No top carry slot is written, so the reversal skips that level too.

    Savings against ``invented:compare-carry`` (same width, same layout):
    T -1 and G -1 at every size, plus depth -1. Against the textbook
    ``known:compare-ripple``: T -1, G -4, D -1 at every size.

    Cost: W = 2n + 2, T = 2n - 1, G = 8n, D = 4n + 4.

    Verified exhaustively on all 2^(2n+1) inputs for n = 1..6 (width 14 at
    n = 6 is the sandbox dense limit); cost formula pinned to n = 10.
    """
    flag = 2 * n
    anc = flag + 1                      # c_0 slot, |0> in and out
    c = Circuit(2 * n + 2)
    g: list = [X(i) for i in range(n)]
    chain: list = []                    # c_{i+1} written into y_i, i = 0..n-2
    for i in range(n - 1):
        ci = anc if i == 0 else (n + i - 1)
        chain += [CNOT(n + i, i), CNOT(n + i, ci), Toffoli(ci, i, n + i)]
    g += chain
    # Top step: flag ^= maj(c_{n-1}, ~x_{n-1}, y_{n-1}) with no carry written.
    xt, yt = n - 1, 2 * n - 1
    ct = anc if n == 1 else 2 * n - 2
    g += [CNOT(xt, yt), CNOT(xt, ct), CNOT(xt, flag),
          Toffoli(yt, ct, flag), CNOT(xt, ct), CNOT(xt, yt)]
    g += list(reversed(chain))
    g += [X(i) for i in range(n)]
    c.gates = g
    return c


# ---------------------------------------------------------------------------
# 8. Majority vote: reachability-pruned counter + OR-marker threshold.
# ---------------------------------------------------------------------------

def _pruned_inc(i: int, base: int, slot: int) -> list:
    """Add x_i into the counter at ``base`` using only live carry levels.

    Before the i-th increment the counter holds v <= i, so the carry into
    level j is provably 0 whenever 2^j > i + 1: only J = floor(log2(i+1))
    levels can ever fire.  Level 2 (if live) is written straight into the
    counter's top bit, and the dead levels above it are never allocated.
    """
    J = (i + 1).bit_length() - 1
    g: list = []
    if J == 1:
        g.append(Toffoli(i, base, base + 1))          # top-write c_1 into a_1
    elif J >= 2:
        g.append(Toffoli(i, base, slot))              # c_1 = x_i & a_0
        for j in range(1, J - 1):
            g.append(Toffoli(slot + j - 1, base + j, slot + j))
        g.append(Toffoli(slot + J - 2, base + J - 1, base + J))  # top-write c_J
        for j in range(J - 1, 0, -1):
            g.append(CNOT(slot + j - 1, base + j))
            if j >= 2:
                g.append(Toffoli(slot + j - 2, base + j - 1, slot + j - 1))
            else:
                g.append(Toffoli(i, base, slot))
    g.append(CNOT(i, base))                           # a_0 ^= x_i
    return g


def majority_pruned(n: int) -> Circuit:
    """Majority oracle with a reachability-pruned popcount counter.

    Contract (``majority_spec``): x = bits 0..n-1, flag = bit n; the map is
    flag ^= [popcount(x) >= ceil(n/2)].

    Two independent savings over ``known:majority-vote``:

    1. **Reachability pruning.** The reference runs all m-1 carry levels on
       every one of the n increments. But before the i-th increment the
       counter is at most i, so level j of the carry is provably zero for
       2^j > i + 1 and its Toffoli is a no-op on every basis input. Keeping
       only J = floor(log2(i+1)) levels removes the gates outright rather
       than optimizing them, which is why the saving grows with n.
    2. **Top-write on the last live level.** The last live carry is written
       straight into the counter bit above it; there is no slot to store and
       no second Toffoli to uncompute.

    The threshold step reuses ``threshold_flag`` in OR-marker mode, which
    folds the flag write into the last carry level (polarity off: ``maj(a, 0,
    c) = a & c`` is a Toffoli, so the final level needs no extra CNOT).

    Cost at n = 2..12 (W/T/G/D): (5,3,9,8), (6,4,11,10), (9,11,23,20),
    (10,19,35,34), (11,25,45,44), (12,28,51,50), (15,39,69,66),
    (16,53,93,90), (17,63,109,106), (18,71,119,116), (19,81,135,132).
    Gains vs the reference at n = 10: T 63 < 126, G 109 < 217, D 106 < 213,
    W 17 < 19; vs the tuned fixed-width baseline: T 63 < 105, G 109 < 171,
    D 106 < 168 at equal width.

    Verified exhaustively on all 2^n inputs for n = 1..7 (width 14 at n = 7
    is the sandbox dense limit); cost profile pinned to n = 12.
    """
    if n == 1:
        return Circuit(2, [CNOT(0, 1)])
    m = popcount_m(n)
    flag, base = n, n + 1
    slot = base + m
    inc: list = []
    for i in range(n):
        inc += _pruned_inc(i, base, slot)
    mark = threshold_flag(tuple(range(base, base + m)), majority_K(n), flag,
                          slot, fuse=True, polarity=False)
    gates = inc + mark + list(reversed(inc))
    width = max(gate.targets()[0] for gate in gates) + 1
    return Circuit(max(width, flag + 1), gates)


def majority_pruned_candidate() -> Candidate:
    return Candidate(
        "invented:majority-pruned",
        "B",
        majority_pruned,
        majority_spec,
        lambda n: n + 1,
        lambda n: [] if n == 1 else list(range(n + 1, majority_pruned(n).n)),
    )


# ---------------------------------------------------------------------------
# 9. Weighted threshold: reachability-pruned weighted accumulator.
# ---------------------------------------------------------------------------

def _pruned_cadd(q: int, p: int, base: int, slot: int, t: int,
                 bound_before: int) -> list:
    """Controlled add of 2^p into the accumulator, live carry levels only.

    ``bound_before`` is the largest value the accumulator can hold before this
    term is added, so the carry into level j is provably 0 whenever
    2^j > bound_before + 2^p.  Levels above L = floor(log2(bound_before+2^p))
    are never allocated; the topmost live carry is written straight into the
    accumulator bit L (top-write), so the chain needs no slot there and no
    uncompute Toffoli.
    """
    L = (bound_before + (1 << p)).bit_length() - 1
    if L > t - 1:
        L = t - 1
    if L <= p:                                   # no live carry at all
        return [CNOT(q, base + p)]
    if L == p + 1:                               # single level: pure top-write
        return [Toffoli(q, base + p, base + L), CNOT(q, base + p)]

    def c(j: int) -> int:
        return slot + j - p - 1

    seq: list = [Toffoli(q, base + p, c(p + 1))]  # c_{p+1} = q & a_p
    for j in range(p + 1, L - 1):                 # c_{j+1} = a_j & c_j
        seq.append(Toffoli(base + j, c(j), c(j + 1)))
    seq.append(Toffoli(base + L - 1, c(L - 1), base + L))  # top-write c_L
    for j in range(L - 1, p, -1):                 # sums top-down + uncompute
        seq.append(CNOT(c(j), base + j))
        if j - 1 == p:
            seq.append(Toffoli(q, base + p, c(j)))
        else:
            seq.append(Toffoli(base + j - 1, c(j - 1), c(j)))
    seq.append(CNOT(q, base + p))                 # a_p ^= q
    return seq


def weighted_pruned(n: int) -> Circuit:
    """Weighted-threshold oracle with a reachability-pruned accumulator.

    Contract (``weighted_threshold_spec``): x = bits 0..n-1, flag = bit n,
    flag ^= [sum_i (i+1) * x_i >= theta] with theta = floor(n(n+1)/4).

    The textbook oracle runs a full t-bit controlled ripple add for every
    binary digit of every weight, on every input.  But the accumulator starts
    at zero and grows by at most one term at a time, so before the term
    carrying weight 2^p the accumulator is bounded by the sum of the terms
    already added.  Every carry level above floor(log2(bound_before + 2^p))
    is then a provable no-op on all basis inputs and is deleted, not
    optimized: the same reachability argument as ``majority_pruned``, applied
    to a mixed-radix sum.  The topmost live carry is top-written into the
    accumulator, removing its slot and its uncompute Toffoli; the threshold
    step then reuses the same carry region in OR-marker mode.

    Cost at n = 2..12 (W/T/G/D): (5,1,7,5), (8,11,23,20), (11,15,33,26),
    (12,27,49,38), (14,43,79,68), (16,73,123,110), (18,81,143,124),
    (20,105,177,158), (21,127,211,188), (24,171,291,262), (25,195,321,292).
    At n = 10 the textbook oracle costs (23,262,433,387) and the tuned
    fixed-width baseline (21,227,359,336); this construction is (21,127,211,188)
    -- weak dominance on all four axes with strict depth, gates and Toffolis.

    Verified exhaustively on all 2^n inputs for n = 1..6 (width 14 at n = 6 is
    the sandbox dense limit); cost profile pinned to n = 12.
    """
    if n == 1:  # theta = 0: [s >= 0] is always 1, flag flips unconditionally
        return Circuit(2, [X(1)])
    t, theta = weighted_t(n), weighted_theta(n)
    flag, base = n, n + 1
    slot = base + t
    adds: list = []
    bound = 0
    for i in range(n):
        w = i + 1
        for p in range(w.bit_length()):
            if (w >> p) & 1:
                adds += _pruned_cadd(i, p, base, slot, t, bound)
                bound += 1 << p
    mark = threshold_flag(tuple(range(base, base + t)), theta, flag, slot,
                          fuse=True, polarity=False)
    gates = adds + mark + list(reversed(adds))
    width = max(gate.targets()[0] for gate in gates) + 1
    return Circuit(max(width, flag + 1), gates)


def weighted_pruned_candidate() -> Candidate:
    return Candidate(
        "invented:weighted-pruned",
        "B",
        weighted_pruned,
        weighted_threshold_spec,
        lambda n: n + 1,
        lambda n: [] if n == 1 else list(range(n + 1, weighted_pruned(n).n)),
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
            (_bind("compare", "invented:compare-topcarry", compare_topcarry),
             (1, 2, 3, 4, 5, 6)),
        ],
        "less_than": [
            (_bind("less_than", "invented:lessthan-fused", lessthan_fused),
             (1, 2, 3, 4, 5, 6, 7, 8, 9)),
        ],
        "majority": [
            (_bind("majority", "invented:majority-pruned", majority_pruned),
             (1, 2, 3, 4, 5, 6, 7)),
        ],
        "weighted_threshold": [
            (_bind("weighted_threshold", "invented:weighted-pruned", weighted_pruned),
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
