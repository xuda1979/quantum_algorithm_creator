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

    def implementation(self, id: str, track: str, build_fn) -> Candidate:
        """Bind another circuit generator to this problem's specification and layout."""
        def ancillas(n: int) -> list[int]:
            return list(range(self.n_data(n), build_fn(n).n))

        return Candidate(id, track, build_fn, self.spec, self.n_data, ancillas)


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


# ---------------------------------------------------------------------------
# Decrement (x -> (x-1) mod 2^n) — textbook complement-wrap baseline
# ---------------------------------------------------------------------------

def build_decrement_wrap_reference(n: int) -> Circuit:
    """Textbook decrementer via the identity x - 1 = NOT(NOT(x) + 1).

    Wraps the ancilla-assisted incrementer in full-width X conjugation.
    Real-world role: step-down arithmetic in QPE chemistry loops and
    amplitude estimation (e.g. iterating occupation numbers downward).

    Toffoli cost 2n-4 (n>=2); the X-wrap adds 2n single-qubit gates.
    Width 2n-1.
    """
    inner = build_increment_ancilla_reference(n)
    gates = [X(i) for i in range(n)] + list(inner.gates) + [X(i) for i in range(n)]
    return Circuit(inner.n, gates)


def decrement_spec(n: int) -> dict[int, int]:
    """Ground truth: x -> (x-1) mod 2^n on the data bits."""
    return {x: (x - 1) % (1 << n) for x in range(1 << n)}


def decrement_candidate_wrap() -> Candidate:
    return Candidate(
        "known:decrement-wrap",
        "A",
        build_decrement_wrap_reference,
        decrement_spec,
        lambda n: n,
        lambda n: list(range(n, 2 * n - 1)) if n > 1 else [],
    )


# ---------------------------------------------------------------------------
# Add classical constant k (x -> (x+k) mod 2^n) — ripple-carry baseline
# ---------------------------------------------------------------------------

def addconst_k(n: int) -> int:
    """The classical constant added by this family: k = n (size-derived)."""
    return n


def build_addconst_carry_reference(n: int, k: int | None = None) -> Circuit:
    """Textbook ripple-carry constant adder: x -> (x+k) mod 2^n.

    Carry recurrence c_{j+1} = maj(x_j, k_j, c_j) into ancillas C_1..C_{n-1}
    (C_j at qubit n+j-1), sum bits applied top-down interleaved with carry
    uncompute, X(0)-last. Real-world role: adding fixed classical offsets
    (nuclear-charge terms in chemistry Hamiltonians, strike-price
    adjustments in finance oracles).

    Toffoli cost ~2n-4 (n>=2), width 2n-1.
    """
    k = addconst_k(n) if k is None else k
    if n == 1:
        return Circuit(1, [X(0)] if k & 1 else [])
    total = 2 * n - 1
    c = Circuit(total)

    def C(j: int) -> int:  # ancilla slot for carry c_j
        return n + j - 1

    kb = [(k >> j) & 1 for j in range(n)]

    # compute carries bottom-up
    if kb[0]:
        c.gates.append(CNOT(0, C(1)))  # c_1 = x_0
    for j in range(1, n - 1):
        if kb[j]:  # c_{j+1} = x_j | c_j
            c.gates.append(CNOT(j, C(j + 1)))
            c.gates.append(CNOT(C(j), C(j + 1)))
            c.gates.append(Toffoli(j, C(j), C(j + 1)))
        else:  # c_{j+1} = x_j & c_j
            c.gates.append(Toffoli(j, C(j), C(j + 1)))

    # apply sums top-down, interleaved with carry uncompute
    for j in range(n - 2, 0, -1):
        c.gates.append(CNOT(C(j + 1), j + 1))  # s_{j+1} ^= c_{j+1}
        if kb[j + 1]:
            c.gates.append(X(j + 1))
        if kb[j]:  # uncompute c_{j+1}
            c.gates.append(CNOT(j, C(j + 1)))
            c.gates.append(CNOT(C(j), C(j + 1)))
            c.gates.append(Toffoli(j, C(j), C(j + 1)))
        else:
            c.gates.append(Toffoli(j, C(j), C(j + 1)))
    c.gates.append(CNOT(C(1), 1))  # s_1 ^= c_1
    if kb[1]:
        c.gates.append(X(1))
    if kb[0]:
        c.gates.append(CNOT(0, C(1)))  # uncompute c_1
    if kb[0]:
        c.gates.append(X(0))  # s_0 ^= k_0 LAST
    return c


def addconst_spec(n: int) -> dict[int, int]:
    """Ground truth: x -> (x+n) mod 2^n on the data bits."""
    k = addconst_k(n)
    return {x: (x + k) % (1 << n) for x in range(1 << n)}


def addconst_candidate_carry() -> Candidate:
    return Candidate(
        "known:addconst-carry",
        "A",
        build_addconst_carry_reference,
        addconst_spec,
        lambda n: n,
        lambda n: list(range(n, 2 * n - 1)) if n > 1 else [],
    )


# ---------------------------------------------------------------------------
# Two's-complement negation (x -> (-x) mod 2^n) — X-wrapped increment baseline
# ---------------------------------------------------------------------------

def build_negate_wrap_reference(n: int) -> Circuit:
    """Textbook negater via the identity -x = NOT(x) + 1 (mod 2^n).

    Wraps the ancilla-assisted incrementer in leading full-width X (the
    trailing +1 needs no conjugation). Real-world role: signed arithmetic
    inside finance payoff oracles and lattice/QFT arithmetic blocks.

    Toffoli cost 2n-4 (n>=2); leading X-wrap adds n single-qubit gates.
    Width 2n-1.
    """
    if n == 1:
        return Circuit(1)  # -x = x mod 2: 1-bit negation is the identity
    inner = build_increment_ancilla_reference(n)
    gates = [X(i) for i in range(n)] + list(inner.gates)
    return Circuit(inner.n, gates)


def negate_spec(n: int) -> dict[int, int]:
    """Ground truth: x -> (-x) mod 2^n on the data bits."""
    return {x: (-x) % (1 << n) for x in range(1 << n)}


def negate_candidate_wrap() -> Candidate:
    return Candidate(
        "known:negate-wrap",
        "A",
        build_negate_wrap_reference,
        negate_spec,
        lambda n: n,
        lambda n: list(range(n, 2 * n - 1)) if n > 1 else [],
    )


# ---------------------------------------------------------------------------
# Prefix-XOR cascade (y_i = b_0 ^ ... ^ b_i) — serial CNOT ladder baseline
# ---------------------------------------------------------------------------

def build_prefixxor_ladder_reference(n: int) -> Circuit:
    """Textbook prefix-XOR (running parity) via a serial CNOT ladder.

    Bottom-up b_i ^= b_{i-1}: after the cascade bit i holds the running
    parity P_i = b_0 ^ ... ^ b_i. Reversing the gates takes adjacent
    differences and recovers the original bits. Real-world role: QEC
    syndrome parity checks and checksum/hash layers inside oracles.

    n-1 CNOTs, depth n-1, 0 Toffolis, 0 ancillas.
    """
    c = Circuit(n)
    for i in range(1, n):
        c.gates.append(CNOT(i - 1, i))
    return c


def prefixxor_spec(n: int) -> dict[int, int]:
    """Ground truth: y_i = b_0 ^ ... ^ b_i (running parity), little-endian."""
    out = {}
    for x in range(1 << n):
        y, p = 0, 0
        for i in range(n):
            p ^= (x >> i) & 1
            y |= p << i
        out[x] = y
    return out


def prefixxor_candidate_ladder() -> Candidate:
    return Candidate(
        "known:prefixxor-ladder",
        "A",
        build_prefixxor_ladder_reference,
        prefixxor_spec,
        lambda n: n,
        lambda n: [],
    )


# ---------------------------------------------------------------------------
# Less-than-constant flag oracle (f ^= [x < k]) — borrow-chain baseline
# ---------------------------------------------------------------------------

def lessthan_k(n: int) -> int:
    """Threshold constant k = min(n+1, 2^(n-1) - 1)."""
    m = n - 1
    return min(n + 1, (1 << m) - 1)


def build_lessthan_borrow_reference(n: int) -> Circuit:
    """Textbook ripple-borrow comparator against classical constant k.

    Data layout: bits 0..n-2 hold x (m = n-1 bits), bit n-1 holds flag f
    with XOR-into-flag oracle semantics f ^= [x < k]. Borrow chain kept in
    no-borrow form D_j = [x_{<j} >= k_{<j}]: D_1 = ~k_0 | x_0, then
    D_{j+1} = x_j | D_j if k_j = 0, else x_j & D_j. Flag = ~D_m; chain
    uncomputed by reversal. Real-world role: risk-threshold oracles in
    finance (VaR / limit checks) and ML classifier oracles.

    Width 2n-2 (n>=2); Toffolis ~ #(k_j=0 steps), n-2 ancillas.
    """
    m = n - 1
    if m < 1 or lessthan_k(n) == 0:
        return Circuit(n)
    k = lessthan_k(n)
    kb = [(k >> j) & 1 for j in range(m)]
    width = n + (m - 1) if m >= 2 else n
    c = Circuit(width)
    flag = n - 1

    def slot(j: int) -> int:  # slot for D_j (j >= 2)
        return n + j - 2

    # D_1: ('c1',) constant one, or ('b0',) qubit 0
    d1 = "b0" if kb[0] else "c1"
    compute: list = []
    for j in range(1, m):
        if kb[j] == 0:  # D_{j+1} = x_j | D_j
            if d1 == "c1":
                continue  # still constant 1
            src = 0 if d1 == "b0" else slot(j)
            # OR into a zeroed slot by inclusion-exclusion: a|b = a^b^(a&b)
            compute += [CNOT(j, slot(j + 1)), CNOT(src, slot(j + 1)),
                        Toffoli(j, src, slot(j + 1))]
            d1 = f"s{slot(j + 1)}"
        else:  # D_{j+1} = x_j & D_j
            if d1 == "c1":
                compute += [CNOT(j, slot(j + 1))]
            elif d1 == "b0":
                compute += [Toffoli(j, 0, slot(j + 1))]
            else:
                compute += [Toffoli(j, slot(j), slot(j + 1))]
            d1 = f"s{slot(j + 1)}"
    # flag ^= B_m = ~D_m
    if d1 == "b0":  # m == 1 and k_0 == 1
        c.gates += [X(0), CNOT(0, flag), X(0)]
    elif d1.startswith("s"):
        q = int(d1[1:])
        c.gates += compute + [CNOT(q, flag), X(flag)] + list(reversed(compute))
    # d1 == 'c1': B_m = 0, flag never flips
    return c


def lessthan_spec(n: int) -> dict[int, int]:
    """Ground truth: f ^= [x < k] with x in bits 0..n-2, f = bit n-1."""
    m = n - 1
    k = lessthan_k(n)
    mask = (1 << m) - 1
    out = {}
    for z in range(1 << n):
        x, f = z & mask, z >> (n - 1) if n > 1 else 0
        f ^= 1 if x < k else 0
        out[z] = x | (f << (n - 1)) if n > 1 else z
    return out


def lessthan_candidate_borrow() -> Candidate:
    return Candidate(
        "known:lessthan-borrow",
        "A",
        build_lessthan_borrow_reference,
        lessthan_spec,
        lambda n: n,
        lambda n: list(range(n, n + max(0, n - 2))),
    )


def popcount_m(n: int) -> int:
    """Counter width: m = ceil(log2(n+1)) bits (holds weights 0..n)."""
    m = 1
    while (1 << m) < n + 1:
        m += 1
    return m


def build_popcount_reference(n: int) -> Circuit:
    """Textbook popcount: each data bit controls a ripple increment of the
    m-bit counter (VBE-style carry chain, interleaved top-down apply/uncompute,
    one shared carry-ancilla register).

    Data layout: bits 0..n-1 hold x, bits n..n+m-1 hold counter c. Map is a
    full permutation: c -> (c + popcount(x)) mod 2^m. Real-world role: QEC
    syndrome-weight counting (correctable-vs-uncorrectable flags), ML feature
    counting, Hamming-distance oracles.

    Width n + 2m - 1; Toffolis 2n(m-1); m-1 ancillas.
    """
    m = popcount_m(n)
    width = n + m + (m - 1)
    c = Circuit(width)
    base = n  # counter qubit c_j at base + j
    g = n + m  # carry ancilla g_j (j = 1..m-1) at g + j - 1
    for i in range(n):
        if m == 1:  # 1-bit counter: controlled increment is a single CNOT
            c.gates.append(CNOT(i, base))
            continue
        # controlled increment of counter by 1, control = data bit i:
        # carry chain bottom-up: g_j = x_i & c_0 & ... & c_{j-1}
        c.gates.append(Toffoli(i, base, g))
        for j in range(1, m - 1):
            c.gates.append(Toffoli(g + j - 1, base + j, g + j))
        # sums top-down, interleaved with carry uncompute (old c_j intact)
        for j in range(m - 1, 0, -1):
            c.gates.append(CNOT(g + j - 1, base + j))
            if j >= 2:
                c.gates.append(Toffoli(g + j - 2, base + j - 1, g + j - 1))
            else:
                c.gates.append(Toffoli(i, base, g))
        c.gates.append(CNOT(i, base))
    return c


def popcount_spec(n: int) -> dict[int, int]:
    """Ground truth: c -> (c + popcount(x)) mod 2^m over all counter values."""
    m = popcount_m(n)
    mask_x = (1 << n) - 1
    out = {}
    for z in range(1 << (n + m)):
        x = z & mask_x
        ctr = z >> n
        ctr = (ctr + bin(x).count("1")) % (1 << m)
        out[z] = x | (ctr << n)
    return out


def popcount_candidate_increment() -> Candidate:
    return Candidate(
        "known:popcount-ripple",
        "A",
        build_popcount_reference,
        popcount_spec,
        lambda n: n + popcount_m(n),
        lambda n: list(range(n + popcount_m(n), n + 2 * popcount_m(n) - 1)),
    )


def build_compare_ripple_reference(n: int) -> Circuit:
    """Textbook Cuccaro-style ripple comparator: flag ^= [x < y] over two
    n-bit registers, ONE ancilla (carry-in), exact-reversal uncompute.

    Identity: carry out of (x + ~y + 1) = [x >= y], so flag ^= carry ^ 1
    gives [x < y]. The carry chain uses the reversible majority gate
    MAJ(c, b, a) = CNOT(a,b); CNOT(a,c); Toffoli(c,b,a), which computes
    a <- maj(c,b,a) in place (parities saved into b, c keep it injective —
    a bare borrow update b' = maj(~y,x,b) is NOT injective). Chain:
    c_{i+1} = maj(~y_i, x_i, c_i); final carry lands in y_{n-1}; the whole
    chain is then reversed to restore x, y and the ancilla.

    Data layout: x = bits 0..n-1, y = bits n..2n-1, flag = bit 2n,
    ancilla = bit 2n+1. Real-world role: optimization oracles (bound
    comparisons in QAOA / branch-and-bound), quantum sorting, finance
    limit-order checks.

    Width 2n+2; Toffolis 2n; CNOTs 4n+1; 1 ancilla.
    """
    flag = 2 * n
    anc = flag + 1
    c = Circuit(2 * n + 2)
    maj: list = []
    for i in range(n):
        ci = anc if i == 0 else (n + i - 1)  # carry-in qubit
        xi, yi = i, n + i
        maj += [CNOT(yi, xi), CNOT(yi, ci), Toffoli(ci, xi, yi)]
    c.gates += [X(n + i) for i in range(n)]  # y <- ~y
    c.gates.append(X(anc))                   # carry-in 1 (the +1)
    c.gates += maj                           # y_{n-1} now holds [x >= y]
    c.gates += [CNOT(n + n - 1, flag), X(flag)]  # flag ^= [x < y]
    c.gates += list(reversed(maj))           # restore carries/parities
    c.gates += [X(n + i) for i in range(n)]  # restore y
    c.gates.append(X(anc))                   # restore ancilla
    return c


def compare_spec(n: int) -> dict[int, int]:
    """Ground truth: f ^= [x < y] with x = bits 0..n-1, y = bits n..2n-1."""
    out = {}
    mask = (1 << n) - 1
    for z in range(1 << (2 * n + 1)):
        x = z & mask
        y = (z >> n) & mask
        f = z >> (2 * n)
        f ^= 1 if x < y else 0
        out[z] = x | (y << n) | (f << (2 * n))
    return out


def compare_candidate_ripple() -> Candidate:
    return Candidate(
        "known:compare-ripple",
        "A",
        build_compare_ripple_reference,
        compare_spec,
        lambda n: 2 * n + 1,
        lambda n: [2 * n + 1],
    )


def build_ctrl_increment_reference(n: int) -> Circuit:
    """Textbook controlled incrementer: x -> x + q (mod 2^n) controlled by
    q — the addconst ripple (k = 1) with every constant-dependent gate
    control-lifted onto q.

    Data layout: x = bits 0..n-1, control q = bit n, carry ancillas
    C(j) = bit n+j (j = 1..n-1). Map is a full permutation
    (x, q) -> (x + q mod 2^n, q). Real-world role: Grover / amplitude-
    amplification oracles, controlled-Hamiltonian rotation steps,
    controlled offset arithmetic in QPE.

    Width 2n; Toffolis 2(n-1); n-1 ancillas.
    """
    if n == 1:
        return Circuit(2, [CNOT(1, 0)])
    q = n
    c = Circuit(2 * n)

    def C(j: int) -> int:  # carry slot c_j
        return n + j

    c.gates.append(Toffoli(0, q, C(1)))  # c_1 = x_0 & q
    for j in range(1, n - 1):  # c_{j+1} = x_j & c_j
        c.gates.append(Toffoli(j, C(j), C(j + 1)))
    for j in range(n - 2, 0, -1):  # sums top-down, carries uncomputed
        c.gates.append(CNOT(C(j + 1), j + 1))
        c.gates.append(Toffoli(j, C(j), C(j + 1)))
    c.gates.append(CNOT(C(1), 1))
    c.gates.append(Toffoli(0, q, C(1)))  # uncompute c_1
    c.gates.append(CNOT(q, 0))  # s_0 ^= q LAST
    return c


def ctrl_increment_spec(n: int) -> dict[int, int]:
    """Ground truth: (x, q) -> (x + q mod 2^n, q), q = bit n."""
    mask = (1 << n) - 1
    out = {}
    for z in range(1 << (n + 1)):
        x, q = z & mask, z >> n
        out[z] = ((x + q) % (1 << n)) | (q << n)
    return out


def ctrl_increment_candidate_ripple() -> Candidate:
    return Candidate(
        "known:ctrl-increment-ripple",
        "A",
        build_ctrl_increment_reference,
        ctrl_increment_spec,
        lambda n: n + 1,
        lambda n: list(range(n + 1, 2 * n)),
    )


def weighted_t(n: int) -> int:
    """Accumulator width: smallest t with 2^t > total weight n(n+1)/2."""
    total = n * (n + 1) // 2
    t = 1
    while (1 << t) <= total:
        t += 1
    return t


def weighted_theta(n: int) -> int:
    """Threshold: half the total weight (weighted-majority cut)."""
    return (n * (n + 1) // 2) // 2


def _cadd_pow2_seq(q: int, p: int, a_base: int, t: int, carry_base: int) -> list:
    """Controlled add of 2^p (control q) into the t-bit accumulator at
    a_base, using dedicated carry slots c_j = carry_base + j - 1
    (j = 1..t-1; all zero on entry and exit; verified ripple pattern)."""
    def c(j: int) -> int:
        return carry_base + j - 1

    seq: list = []
    if p == t - 1:
        seq.append(CNOT(q, a_base + t - 1))
        return seq
    seq.append(Toffoli(q, a_base + p, c(p + 1)))  # c_{p+1} = q & a_p
    for j in range(p + 1, t - 1):  # c_{j+1} = a_j & c_j
        seq.append(Toffoli(a_base + j, c(j), c(j + 1)))
    for j in range(t - 1, p, -1):  # sums top-down + carry uncompute
        seq.append(CNOT(c(j), a_base + j))
        if j - 1 == p:
            seq.append(Toffoli(q, a_base + p, c(j)))
        else:
            seq.append(Toffoli(a_base + j - 1, c(j - 1), c(j)))
    seq.append(CNOT(q, a_base + p))  # s_p ^= q
    return seq


def _threshold_seq(K: int, a_base: int, t: int, flag: int, carry_base: int) -> list:
    """flag ^= carry-out of acc + K (fires iff acc >= 2^t - K); the
    constant-add carry chain c_1 = a_0 & K_0, c_{j+1} = maj(a_j, K_j, c_j)
    is exactly reversed, so the accumulator and all carry slots come back
    unchanged and only the flag flip persists."""
    def c(j: int) -> int:
        return carry_base + j - 1

    up: list = []
    if K & 1:
        up.append(CNOT(a_base, c(1)))  # c_1 = a_0
    for j in range(1, t):  # c_{j+1} = maj(a_j, K_j, c_j)
        if (K >> j) & 1:
            # maj(a, 1, c) = a | c = a ^ c ^ (a & c)
            up += [CNOT(a_base + j, c(j + 1)), CNOT(c(j), c(j + 1)),
                   Toffoli(a_base + j, c(j), c(j + 1))]
        else:
            up.append(Toffoli(a_base + j, c(j), c(j + 1)))
    return up + [CNOT(c(t), flag)] + list(reversed(up))


def build_weighted_threshold_reference(n: int) -> Circuit:
    """Textbook weighted-threshold oracle: flag ^= [sum_i (i+1)*x_i >= theta]
    (weights 1..n, theta = half the total weight) — the QUBO / knapsack
    constraint oracle used in QAOA and portfolio selection.

    Construction: serial controlled adds of each weight into a t-bit
    accumulator (dedicated per-level carry slots, the verified ripple
    pattern), then flag ^= carry-out of acc + (2^t - theta) — the
    carry-out fires iff acc >= theta — then all weighted adds are
    reversed to restore the accumulator (it is an ancilla, returns to 0).

    Data layout: x = bits 0..n-1, flag = bit n, accumulator = bits
    n+1..n+t, carry slots c_1..c_t = bits n+1+t..n+2t. Width n+2t+1
    (n >= 2); Toffolis ~ 2*sum_i (t-1 - popcount(i+1)) + O(t).

    Real-world role: portfolio optimization (budget constraints), knapsack
    QUBO oracles, Ising constraint penalties.
    """
    if n == 1:  # theta = 0: [s >= 0] = 1, flag flips unconditionally
        return Circuit(2, [X(1)])
    t = weighted_t(n)
    K = (1 << t) - weighted_theta(n)
    flag = n
    a_base = n + 1
    carry_base = a_base + t  # slots for carries c_1..c_t
    width = carry_base + t
    adds = [_cadd_pow2_seq(i, p, a_base, t, carry_base)
            for i in range(n) for p in range(t) if (i + 1) >> p & 1]
    gates: list = []
    for seq in adds:  # acc = sum_i (i+1) x_i  (persists)
        gates += seq
    gates += _threshold_seq(K, a_base, t, flag, carry_base)
    for seq in reversed(adds):  # restore accumulator to 0
        gates += list(reversed(seq))
    return Circuit(width, gates)


def weighted_threshold_spec(n: int) -> dict[int, int]:
    """Ground truth: f ^= [sum_i (i+1)*x_i >= theta], x = bits 0..n-1,
    f = bit n."""
    theta = weighted_theta(n)
    out = {}
    for z in range(1 << (n + 1)):
        x, f = z & ((1 << n) - 1), z >> n
        s = sum((i + 1) * ((x >> i) & 1) for i in range(n))
        out[z] = x | ((f ^ (1 if s >= theta else 0)) << n)
    return out


def weighted_threshold_candidate_qubo() -> Candidate:
    return Candidate(
        "known:weighted-threshold",
        "A",
        build_weighted_threshold_reference,
        weighted_threshold_spec,
        lambda n: n + 1,
        lambda n: [] if n == 1 else list(range(n + 1, n + 1 + 2 * weighted_t(n))),
    )


def majority_K(n: int) -> int:
    """Majority threshold: ceil(n/2) (at-least-half vote)."""
    return (n + 1) // 2


def build_majority_reference(n: int) -> Circuit:
    """Textbook majority-vote oracle: flag ^= [popcount(x) >= ceil(n/2)]
    (perceptron with equal weights and zero bias, repetition-code
    decoding vote, ensemble-classifier aggregation).

    Construction: ripple popcount of x into an m-bit counter (verified
    VBE pattern, one shared carry register), then flag ^= carry-out of
    counter + (2^m - K) — the carry-out fires iff popcount >= K — then
    the counter increments are reversed (counter is an ancilla, returns
    to 0).

    Data layout: x = bits 0..n-1, flag = bit n, counter = bits n+1..n+m,
    carry slots = bits n+1+m..n+2m. Width n+2m+1 (n >= 2);
    Toffolis 2n(m-1) + O(m).
    """
    if n == 1:  # majority of one bit: flag ^= x_0
        return Circuit(2, [CNOT(0, 1)])
    m = popcount_m(n)
    K = (1 << m) - majority_K(n)
    flag = n
    base = n + 1
    g = n + 1 + m  # carry slots: counter chain g..g+m-2, threshold c_m = g+m-1
    width = g + m
    inc: list = []  # all counter increments (reversed after the threshold)
    for i in range(n):
        inc.append(Toffoli(i, base, g))          # g_1 = x_i & c_0
        for j in range(1, m - 1):                # g_{j+1} = g_j & c_j
            inc.append(Toffoli(g + j - 1, base + j, g + j))
        for j in range(m - 1, 0, -1):            # sums top-down + uncompute
            inc.append(CNOT(g + j - 1, base + j))
            if j >= 2:
                inc.append(Toffoli(g + j - 2, base + j - 1, g + j - 1))
            else:
                inc.append(Toffoli(i, base, g))
        inc.append(CNOT(i, base))                # c_0 ^= x_i
    thr = _threshold_seq(K, base, m, flag, g)
    return Circuit(width, inc + thr + list(reversed(inc)))


def majority_spec(n: int) -> dict[int, int]:
    """Ground truth: f ^= [popcount(x) >= ceil(n/2)], x = bits 0..n-1,
    f = bit n."""
    K = majority_K(n)
    out = {}
    for z in range(1 << (n + 1)):
        x, f = z & ((1 << n) - 1), z >> n
        out[z] = x | ((f ^ (1 if bin(x).count("1") >= K else 0)) << n)
    return out


def majority_candidate_vote() -> Candidate:
    return Candidate(
        "known:majority-vote",
        "A",
        build_majority_reference,
        majority_spec,
        lambda n: n + 1,
        lambda n: [] if n == 1 else list(range(n + 1, n + 1 + 2 * popcount_m(n))),
    )


def hamming_K(n: int) -> int:
    """Hamming-distance threshold: ceil(n/2) (at-least-half mismatch)."""
    return (n + 1) // 2


def build_hamming_distance_reference(n: int) -> Circuit:
    """Textbook Hamming-distance threshold oracle over two n-bit registers:
    flag ^= [popcount(x ^ y) >= ceil(n/2)].

    This is the minimum-distance / syndrome-weight test used by quantum error
    correction (flag the syndrome only when its weight reaches the correction
    capability t = ceil(n/2)), and the Hamming-radius membership test in
    streaming error detection.

    Construction: an XOR layer copies y into x's companion register, a ripple
    popcount accumulates the n mismatch bits into an m-bit counter, the flag
    ^= carry-out of counter + (2^m - K), then the increments are reversed and
    the XOR layer undone.

    Data layout: x = bits 0..n-1, y = bits n..2n-1, flag = bit 2n,
    counter = bits 2n+1..2n+m, carry slots above.  Width 2n+2m+1 (n >= 2).
    """
    if n == 1:  # single-bit distance: d = x ^ y, flag ^= d
        return Circuit(3, [CNOT(0, 1), CNOT(1, 2), CNOT(0, 1)])
    m = popcount_m(n)
    K = (1 << m) - hamming_K(n)
    flag = 2 * n
    base = flag + 1
    g = base + m
    width = g + m
    xor = [CNOT(i, n + i) for i in range(n)]
    inc: list = []
    for i in range(n):
        ctrl = n + i
        inc.append(Toffoli(ctrl, base, g))
        for j in range(1, m - 1):
            inc.append(Toffoli(g + j - 1, base + j, g + j))
        for j in range(m - 1, 0, -1):
            inc.append(CNOT(g + j - 1, base + j))
            if j >= 2:
                inc.append(Toffoli(g + j - 2, base + j - 1, g + j - 1))
            else:
                inc.append(Toffoli(ctrl, base, g))
        inc.append(CNOT(ctrl, base))
    thr = _threshold_seq(K, base, m, flag, g)
    return Circuit(width, xor + inc + thr + list(reversed(inc)) + xor)


def hamming_distance_spec(n: int) -> dict[int, int]:
    """Ground truth: f ^= [popcount(x ^ y) >= ceil(n/2)], x = bits 0..n-1,
    y = bits n..2n-1, f = bit 2n."""
    K, mask = hamming_K(n), (1 << n) - 1
    out = {}
    for z in range(1 << (2 * n + 1)):
        x, y, f = z & mask, (z >> n) & mask, z >> (2 * n)
        d = bin(x ^ y).count("1")
        out[z] = x | (y << n) | ((f ^ (1 if d >= K else 0)) << (2 * n))
    return out


def hamming_distance_candidate_ripple() -> Candidate:
    return Candidate(
        "known:hamming-distance-ripple",
        "A",
        build_hamming_distance_reference,
        hamming_distance_spec,
        lambda n: 2 * n + 1,
        lambda n: [] if n == 1 else list(range(2 * n + 1, build_hamming_distance_reference(n).n)),
    )


class KnownAlgorithmLibrary:
    """Micro GraphRAG: problem key -> verified parameterized generators."""

    def __init__(self):
        self._entries = {
            "increment": increment_candidate_ancilla(),
            "bit_reversal": bit_reversal_candidate(),
            "decrement": decrement_candidate_wrap(),
            "add_const": addconst_candidate_carry(),
            "negate": negate_candidate_wrap(),
            "prefix_xor": prefixxor_candidate_ladder(),
            "less_than": lessthan_candidate_borrow(),
            "popcount": popcount_candidate_increment(),
            "compare": compare_candidate_ripple(),
            "ctrl_increment": ctrl_increment_candidate_ripple(),
            "weighted_threshold": weighted_threshold_candidate_qubo(),
            "majority": majority_candidate_vote(),
            "hamming_distance": hamming_distance_candidate_ripple(),
        }

    def synthesize(self, problem_key: str, n: int) -> Candidate:
        if problem_key not in self._entries:
            raise KeyError(f"no known algorithm for '{problem_key}'")
        return self._entries[problem_key]

    def has(self, problem_key: str) -> bool:
        return problem_key in self._entries

    def candidates(self) -> tuple[Candidate, ...]:
        return tuple(self._entries.values())

    def verify_all(self, sizes=(3, 4, 5)) -> dict[str, VerificationReport]:
        """Sweep every entry through the same finite verification limits."""
        sizes = tuple(sizes)
        return {key: evaluate_candidate(candidate, sizes=sizes)
                for key, candidate in self._entries.items()}
