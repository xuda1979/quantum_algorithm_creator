"""Reversible arithmetic building blocks for the Track B synthesis grammar."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby

from .kickmix import CNOT, Circuit, Gate, Toffoli, X


def controlled_increment(control: int, targets: tuple[int, ...], scratch: int, *, fuse: bool) -> list[Gate]:
    """Add one to the little-endian targets, conditional on control."""
    if not targets:
        return []
    carries = []
    source = control
    carry_count = max(0, len(targets) - 1 - int(fuse))
    for i in range(carry_count):
        carry = Toffoli(source, targets[i], scratch + i)
        carries.append(carry)
        source = carry.t

    gates = list(carries)
    if fuse and len(targets) > 1:
        gates.append(Toffoli(source, targets[-2], targets[-1]))
    for i, carry in reversed(list(enumerate(carries, start=1))):
        gates.extend((CNOT(carry.t, targets[i]), carry))
    gates.append(CNOT(control, targets[0]))
    return gates


def threshold_flag(
    bits: tuple[int, ...], threshold: int, flag: int, scratch: int, *,
    complement: bool = False, fuse: bool = False, polarity: bool = False,
) -> list[Gate]:
    """XOR a constant-threshold predicate into an arbitrary flag value."""
    if threshold <= 0 or threshold >= 1 << len(bits):
        return [X(flag)] if (threshold <= 0) != complement else []

    first = (threshold & -threshold).bit_length() - 1
    source = bits[first]
    negated = False
    compute: list[Gate] = []
    middle: list[Gate] = []
    for i in range(first + 1, len(bits)):
        wire = bits[i]
        is_or = not bool(threshold & (1 << i))
        terminal = fuse and i == len(bits) - 1
        target = flag if terminal else scratch + i - first - 1
        store_negated = polarity and is_or
        flips = [X(wire)] if store_negated else []
        if negated != store_negated:
            flips.append(X(source))
        step: list[Gate] = []
        if is_or and not polarity:
            # The local control can start the OR before the carry arrives.
            step.extend((CNOT(wire, target), CNOT(source, target)))
        step.extend((*flips, Toffoli(source, wire, target), *reversed(flips)))
        (middle if terminal else compute).extend(step)
        source, negated = target, store_negated

    if source != flag:
        middle.append(CNOT(source, flag))
    if negated != complement:
        middle.append(X(flag))
    return compute + middle + list(reversed(compute))


def compact_circuit(data_width: int, gates: list[Gate]) -> Circuit:
    last_wire = max((q for gate in gates for q in gate.targets()), default=-1)
    return Circuit(max(last_wire + 1, data_width), gates)


@dataclass(frozen=True)
class SumProgram:
    """Search choices for weighted sums followed by a coherent threshold."""

    bounded: bool = False
    fuse: bool = False
    pair: bool = False
    order: str = "input"
    marker: str = "ripple"

    def __post_init__(self) -> None:
        if self.order not in ("input", "heavy_first", "low_first", "high_first"):
            raise ValueError(f"unknown term order: {self.order!r}")
        if self.marker not in ("ripple", "polarity", "overflow"):
            raise ValueError(f"unknown threshold marker: {self.marker!r}")


def _sum_schedule(weights: tuple[int, ...], program: SumProgram) -> list[tuple[int, tuple[int, ...]]]:
    terms = [(p, i) for i, w in enumerate(weights) for p in range(w.bit_length()) if w & (1 << p)]
    if program.order == "heavy_first":
        terms.sort(key=lambda term: -weights[term[1]])
    elif program.order == "low_first":
        terms.sort(key=lambda term: term[0])
    elif program.order == "high_first":
        terms.sort(key=lambda term: -term[0])
    group_size = 2 if program.pair else 1
    schedule = []
    for place, group in groupby(terms, key=lambda term: term[0]):
        controls = tuple(i for _, i in group)
        for start in range(0, len(controls), group_size):
            schedule.append((place, controls[start:start + group_size]))
    return schedule


def build_sum_threshold(weights: tuple[int, ...], threshold: int, program: SumProgram) -> Circuit:
    """Implement f ^= [sum(w_i*x_i) >= threshold] on all x and f.

    Bounds are derived from the sum of terms already processed. For each pair
    of equally weighted bits, its parity and conjunction have the same sum
    as the two original bits and the pair contributes at most twice its weight.
    """
    if type(threshold) is not int or any(type(weight) is not int or weight < 0 for weight in weights):
        raise ValueError("expected integer threshold and nonnegative integer weights")
    flag = len(weights)
    total = sum(weights)
    if not 1 <= threshold <= total:
        return Circuit(flag + 1, [X(flag)] if threshold <= 0 else [])
    if flag == 1:
        return Circuit(2, [CNOT(0, 1)])

    overflow = program.marker == "overflow"
    if overflow:
        # A bias of 2^t - threshold makes the high bit the comparison result.
        # The entire biased interval fits in t+1 bits and starts below 2^t.
        max_side = max(threshold, total - threshold + 1)
        width = (max_side - 1).bit_length()
        bias = (1 << width) - threshold
    else:
        width = total.bit_length()
        bias = 0
    accumulator = tuple(range(flag + 1, flag + 1 + width))
    forward_wires = accumulator + (flag,) if overflow else accumulator
    setup = [X(q) for i, q in enumerate(accumulator) if bias & (1 << i)]
    schedule = _sum_schedule(weights, program)
    workspace = flag + 1 + width
    pair_wire = workspace
    scratch = workspace + int(any(len(controls) == 2 for _, controls in schedule))
    forward: list[Gate] = []
    cleanup: list[Gate] = []
    bound = bias
    for place, controls in schedule:
        bound += len(controls) * (1 << place)
        limit = bound.bit_length() if program.bounded else len(forward_wires)
        additions = [(place, controls[0])]
        pair_ops: list[Gate] = []
        if len(controls) == 2:
            a, b = controls
            pair_ops = [Toffoli(a, b, pair_wire), CNOT(a, b)]
            additions = [(place, b), (place + 1, pair_wire)]
        paths = [(forward_wires, forward)]
        if overflow:
            # The low-register inverse uses truncated additions, preserving the flag.
            paths.append((accumulator, cleanup))
        for wires, gates in paths:
            if place >= min(limit, len(wires)):
                continue
            gates.extend(pair_ops)
            for shift, control in additions:
                gates.extend(controlled_increment(control, wires[shift:limit], scratch, fuse=program.fuse))
            gates.extend(reversed(pair_ops))

    if overflow:
        return compact_circuit(flag + 1, setup + forward + list(reversed(cleanup)) + list(reversed(setup)))
    mark = threshold_flag(accumulator, threshold, flag, workspace,
                          fuse=program.fuse, polarity=program.marker == "polarity")
    return compact_circuit(flag + 1, forward + mark + list(reversed(forward)))
