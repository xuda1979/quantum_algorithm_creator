"""Exhaustive basis semantics, clean ancillas, and logical resource counts."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, asdict

from .kickmix import CCZ, CNOT, CZ, SWAP, Circuit, Toffoli, X, Z

BasisSpec = Mapping[int, int] | Callable[[int], int]
PACKED_GATES = (X, CNOT, Toffoli, SWAP, Z, CZ, CCZ)
MAX_DENSE_WIDTH = 14
MAX_EXHAUSTIVE_DATA_WIDTH = 14


def verification_sizes(sizes) -> tuple[int, ...]:
    sizes = tuple(sizes)
    if not sizes or any(type(n) is not int or n < 1 for n in sizes):
        raise ValueError("verification requires one or more positive integer sizes")
    return tuple(sorted(set(sizes)))


# --------------------------------------------------------------------------
# Small-n exact statevector evaluator (design doc section 2.3, row 3)
# --------------------------------------------------------------------------

def run_statevector(circuit: Circuit) -> list[complex]:
    """Apply circuit to |0...0> and return the exact statevector (len 2^n)."""
    circuit.validate()
    if circuit.n > MAX_DENSE_WIDTH:
        raise ValueError(f"width {circuit.n} exceeds statevector limit {MAX_DENSE_WIDTH}")
    psi = [0j] * (1 << circuit.n)
    psi[0] = 1.0 + 0j
    for g in circuit.gates:
        g.apply_statevector(psi, circuit.n)
    return psi


def unitary_matrix(circuit: Circuit) -> list[list[complex]]:
    """Full 2^n x 2^n unitary by applying to each basis state (n <= 8)."""
    circuit.validate()
    if circuit.n > 8:
        raise ValueError("full unitary is limited to eight qubits")
    dim = 1 << circuit.n
    cols = []
    for col in range(dim):
        psi = [0j] * dim
        psi[col] = 1.0
        for g in circuit.gates:
            g.apply_statevector(psi, circuit.n)
        cols.append(psi)
    return [[cols[c][r] for c in range(dim)] for r in range(dim)]


def fidelity(circuit_a: Circuit, circuit_b: Circuit) -> float:
    """F = |Tr(U_a^dag U_b)| / 2^n for equal-width circuits."""
    if circuit_a.n != circuit_b.n:
        raise ValueError("fidelity requires equal widths")
    ua, ub = unitary_matrix(circuit_a), unitary_matrix(circuit_b)
    dim = 1 << circuit_a.n
    tr = sum(
        ua[r][c].conjugate() * ub[r][c] for r in range(dim) for c in range(dim)
    )
    return abs(tr) / dim


# --------------------------------------------------------------------------
# Ancilla-cleanliness check (generic, exhaustive) — the uncomputation invariant
# --------------------------------------------------------------------------

@dataclass
class AncillaReport:
    clean: bool
    entangled_indices: list = field(default_factory=list)
    failed_inputs: list = field(default_factory=list)


def check_ancilla_clean(
    circuit_builder,
    n: int,
    ancilla: list[int],
    max_n: int = 6,
) -> AncillaReport:
    """circuit_builder(n) -> Circuit over n data + len(ancilla) ancilla qubits.
    Verifies every ancilla returns to |0> for every computational input x."""
    total = n + len(ancilla)
    if total > max_n + 4 or sorted(ancilla) != list(range(n, total)):
        raise ValueError("expected low data wires followed by clean ancilla wires within the width limit")
    circuit = circuit_builder(n)
    circuit.validate()
    if circuit.n != total:
        raise ValueError(f"circuit width {circuit.n} != {total}")
    bad_idx, bad_in = set(), []
    for x in range(1 << n):
        psi = [0j] * (1 << total)
        psi[x] = 1.0
        for g in circuit.gates:
            g.apply_statevector(psi, circuit.n)
        for idx, amp in enumerate(psi):
            if abs(amp) < 1e-9:
                continue
            for a in ancilla:
                if (idx >> a) & 1:
                    bad_idx.add(a)
                    if len(bad_in) < 8:
                        bad_in.append((x, idx))
    return AncillaReport(clean=not bad_idx, entangled_indices=sorted(bad_idx), failed_inputs=bad_in)


# --------------------------------------------------------------------------
# Semantic verification: V|x>|0^a> = |spec(x)>|0^a> exactly, amplitude +1
# --------------------------------------------------------------------------

@dataclass
class SemanticReport:
    ok: bool
    reasons: list = field(default_factory=list)
    ancilla_clean: bool = False


def check_semantics(
    circuit: Circuit,
    spec: BasisSpec,
    data_n: int,
    eps: float = 1e-9,
) -> SemanticReport:
    """Exhaustive check over all 2^data_n inputs on the exact statevector."""
    circuit.validate()
    if type(data_n) is not int or not 0 <= data_n <= circuit.n <= MAX_DENSE_WIDTH:
        raise ValueError("statevector check exceeds its supported data or circuit width")
    if not math.isfinite(eps) or eps <= 0:
        raise ValueError("statevector tolerance must be finite and positive")
    outputs = expected_outputs(spec, data_n)
    reasons: list[str] = []
    ancilla_clean = True
    total = circuit.n
    dim = 1 << total
    for x in range(1 << data_n):
        psi = [0j] * dim
        psi[x] = 1.0
        for g in circuit.gates:
            g.apply_statevector(psi, total)
        support = []
        for idx, amp in enumerate(psi):
            magnitude = abs(amp)
            if not math.isfinite(magnitude):
                return SemanticReport(False, [f"x={x}: nonfinite output amplitude"])
            if magnitude > eps:
                support.append(idx)
        dirty = any(idx >> data_n for idx in support)
        ancilla_clean = ancilla_clean and not dirty
        if len(reasons) >= 5:
            if not ancilla_clean:
                break
            continue
        if len(support) != 1:
            reasons.append(
                f"x={x}: {len(support)} amplitudes in output "
                "(not a basis permutation — dirty ancilla or non-classical map)"
            )
            continue
        idx = support[0]
        if dirty:
            reasons.append(f"x={x}: ancilla bits nonzero in output (idx={idx:#b})")
            continue
        y = outputs[x]
        if idx != y:
            reasons.append(f"x={x}: got {idx}, want {y}")
            continue
        if abs(psi[idx] - 1.0) > eps:
            reasons.append(f"x={x}: amplitude {psi[idx]} != +1 (stray phase)")
    return SemanticReport(ok=not reasons, reasons=reasons, ancilla_clean=ancilla_clean)


def uses_packed_evaluator(circuit: Circuit) -> bool:
    return all(type(gate) in PACKED_GATES for gate in circuit.gates)


def _check_packed_semantics(circuit: Circuit, expected: tuple[int, ...], data_n: int) -> SemanticReport:
    """Propagate every basis input in parallel, with one bit plane per wire.

    Position x in each plane holds that wire's value on input x. A separate
    plane tracks sign, giving an exact check of the clean-ancilla isometry.
    This evaluates the gate algebra independently of the scalar Kickmix code.
    """
    inputs = 1 << data_n
    wires = [0] * circuit.n
    for q in range(data_n):
        block = 1 << q
        pattern = ((1 << block) - 1) << block
        span = block * 2
        while span < inputs:
            pattern |= pattern << span
            span *= 2
        wires[q] = pattern

    ones = (1 << inputs) - 1
    phase = 0
    for gate in circuit.gates:
        operands = gate.targets()
        kind = type(gate)
        if kind in (Z, CZ, CCZ):
            mask = wires[operands[0]]
            for q in operands[1:]:
                mask &= wires[q]
            phase ^= mask
        elif kind is SWAP:
            a, b = operands
            wires[a], wires[b] = wires[b], wires[a]
        else:
            mask = ones
            for q in operands[:-1]:
                mask &= wires[q]
            wires[operands[-1]] ^= mask

    expected_planes = [bytearray((inputs + 7) // 8) for _ in range(data_n)]
    for x, y in enumerate(expected):
        byte, bit = divmod(x, 8)
        while y:
            low = y & -y
            expected_planes[low.bit_length() - 1][byte] |= 1 << bit
            y ^= low

    mismatch = phase
    for q, plane in enumerate(expected_planes):
        mismatch |= wires[q] ^ int.from_bytes(plane, "little")
    for plane in wires[data_n:]:
        mismatch |= plane

    reasons = []
    while mismatch and len(reasons) < 5:
        low = mismatch & -mismatch
        x = low.bit_length() - 1
        y = sum(((plane >> x) & 1) << q for q, plane in enumerate(wires))
        if y >> data_n:
            reasons.append(f"x={x}: ancilla bits nonzero in output (idx={y:#b})")
        elif y != expected[x]:
            reasons.append(f"x={x}: got {y}, want {expected[x]}")
        else:
            reasons.append(f"x={x}: amplitude -1 != +1 (stray phase)")
        mismatch ^= low
    return SemanticReport(ok=not reasons, reasons=reasons, ancilla_clean=not any(wires[data_n:]))


def expected_outputs(spec: BasisSpec, data_n: int) -> tuple[int, ...]:
    if type(data_n) is not int or not 0 <= data_n <= MAX_EXHAUSTIVE_DATA_WIDTH:
        raise ValueError(f"data width {data_n!r} exceeds the exhaustive verification limit")
    size = 1 << data_n
    outputs = tuple(spec[x] if isinstance(spec, Mapping) else spec(x) for x in range(size))
    if any(type(y) is not int or not 0 <= y < size for y in outputs):
        raise ValueError("specification output is outside the data register")
    return outputs

# --------------------------------------------------------------------------
# Symbolic scaling-fit guardrail (design doc section 5, guardrail 1)
# --------------------------------------------------------------------------

def fit_scaling(counts: dict[int, int]) -> dict:
    """Fit polynomial (power-law) vs exponential growth across sizes.

    verdict 'exponential' (e.g. hidden O(2^n) blowup from brute-force
    unrolling) is penalized/rejected by the sandbox.

    Sizes whose count is zero are skipped rather than clamped to a synthetic
    floor: ln(0) is undefined, and clamping it to ln(1e-12) ≈ -27.6 dominates
    the regression, so a legitimately constant-work size (a constant adder
    whose addend is a power of two needs no Toffolis at all) would masquerade
    as exponential blowup. Fitting only positive counts is the honest reading
    of the data; when fewer than three positive sizes remain the guardrail
    reports 'insufficient_data' and does not reject.
    """
    skipped = {k: counts[k] for k in sorted(counts) if counts[k] <= 0}
    ns = sorted(k for k, v in counts.items() if v > 0)
    if len(ns) < 3:
        return {
            "poly_exp": None,
            "poly_r2": None,
            "expo_r2": None,
            "verdict": "insufficient_data",
            "zero_sizes_skipped": skipped,
        }
    ys = [float(counts[k]) for k in ns]
    ln_y = [math.log(y) for y in ys]

    def linfit(xs, ys_):
        n_ = len(xs)
        mx, my = sum(xs) / n_, sum(ys_) / n_
        sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys_))
        sxx = sum((a - mx) ** 2 for a in xs)
        if sxx == 0:
            return 0.0, 0.0
        b = sxy / sxx
        a = my - b * mx
        ss_res = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys_))
        ss_tot = sum((y - my) ** 2 for y in ys_)
        return ((1 - ss_res / ss_tot) if ss_tot else 1.0), b

    ln_ns = [math.log(v) for v in ns]
    powfit = linfit(ln_ns, ln_y)  # ln y = k ln n + c  ->  y = C n^k
    linfit_exp = linfit(ns, ln_y)  # ln y = k n + c  ->  y = C e^{kn}

    if powfit[0] + 1e-9 >= linfit_exp[0]:
        verdict = "polynomial"
    elif (len(ns) >= 4 and linfit_exp[0] >= 0.995 and linfit_exp[0] - powfit[0] > 0.001
          and linfit_exp[1] >= math.log(1.5)):
        verdict = "exponential"
    else:
        verdict = "inconclusive"
    return {
        "poly_exp": round(powfit[1], 4) if powfit[1] else powfit[1],
        "poly_r2": round(powfit[0], 4),
        "expo_r2": round(linfit_exp[0], 4),
        "verdict": verdict,
        "zero_sizes_skipped": skipped,
    }


# --------------------------------------------------------------------------
# The sandbox evaluation contract (typed tool surface)
# --------------------------------------------------------------------------

@dataclass
class VerificationReport:
    """Deterministic Layer-3 output for one candidate."""

    candidate_id: str
    track: str  # "A" or "B"
    sizes: list = field(default_factory=list)
    correctness: dict = field(default_factory=dict)  # n -> bool (semantic)
    ancilla_clean: dict = field(default_factory=dict)  # n -> bool
    toffoli: dict = field(default_factory=dict)  # n -> count
    gate_count: dict = field(default_factory=dict)
    depth: dict = field(default_factory=dict)
    peak_qubits: dict = field(default_factory=dict)
    semantic_backend: dict = field(default_factory=dict)
    scaling: dict = field(default_factory=dict)
    wall_time_s: float = 0.0
    passed: bool = False
    failure_reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_candidate(
    candidate,
    sizes=(3, 4, 5),
    require_poly: bool = True,
    max_total_width: int = 128,
) -> VerificationReport:
    """Full Maker-Checker pass for a parameterized candidate.

    Candidate protocol:
      .id, .track,
      .build(n)      -> Circuit (width = n_data(n) + len(ancilla(n)))
      .spec(n)       -> dict x -> y ground truth on data bits
      .n_data(n)     -> number of data qubits
      .ancilla(n)    -> list of ancilla qubit indices
    """
    t0 = time.perf_counter()
    rep = VerificationReport(candidate_id=candidate.id, track=candidate.track)
    reasons: list[str] = []

    try:
        sizes = verification_sizes(sizes)
    except ValueError as exc:
        rep.failure_reasons = [str(exc)]
        return rep

    for n in sizes:
        try:
            circuit = candidate.build(n)
            circuit.validate()
            data_n = candidate.n_data(n)
            anc = list(candidate.ancilla(n))
            if type(data_n) is not int or not 0 <= data_n <= MAX_EXHAUSTIVE_DATA_WIDTH:
                raise ValueError(f"data width {data_n!r} exceeds the exhaustive verification limit")
            if circuit.n != data_n + len(anc):
                raise ValueError(f"width mismatch circuit={circuit.n} data+anc={data_n + len(anc)}")
            if any(type(q) is not int for q in anc) or sorted(anc) != list(range(data_n, circuit.n)):
                raise ValueError("ancillas must be exactly the wires above the data register")
            if circuit.n > max_total_width:
                raise ValueError(f"width {circuit.n} exceeds sandbox limit {max_total_width}")
            packed = uses_packed_evaluator(circuit)
            if not packed and circuit.n > MAX_DENSE_WIDTH:
                raise ValueError(f"width {circuit.n} exceeds statevector limit {MAX_DENSE_WIDTH}")

            outputs = expected_outputs(candidate.spec(n), data_n)
            if packed:
                sem = _check_packed_semantics(circuit, outputs, data_n)
                rep.semantic_backend[n] = "packed"
            else:
                sem = check_semantics(circuit, outputs.__getitem__, data_n)
                rep.semantic_backend[n] = "statevector"
        except Exception as exc:
            reasons.append(f"n={n}: {type(exc).__name__}: {exc}")
            continue

        rep.sizes.append(n)
        rep.correctness[n] = sem.ok
        rep.ancilla_clean[n] = sem.ancilla_clean
        rep.toffoli[n] = circuit.toffoli_count()
        rep.gate_count[n] = circuit.gate_count()
        rep.depth[n] = circuit.depth()
        rep.peak_qubits[n] = circuit.n
        if not sem.ok:
            reasons.append(f"n={n}: " + "; ".join(sem.reasons[:3]))

    if rep.sizes:
        rep.scaling = fit_scaling(rep.gate_count)
        rep.scaling["width"] = fit_scaling(rep.peak_qubits)
        rep.scaling["toffoli"] = fit_scaling(rep.toffoli)
        if require_poly:
            for metric, fit in (("gates", rep.scaling), ("width", rep.scaling["width"]),
                                ("Toffolis", rep.scaling["toffoli"])):
                if fit["verdict"] == "exponential":
                    reasons.append(f"scaling guardrail: exponential {metric} growth suspected")
    else:
        reasons.append("no sizes evaluated")

    rep.failure_reasons = reasons
    rep.passed = not reasons
    rep.wall_time_s = round(time.perf_counter() - t0, 4)
    return rep
