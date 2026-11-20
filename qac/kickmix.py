"""Layer 3 (part 1): Reversible & phase-kickback ("Kickmix") simulator.

Tracks computational-basis states and phases classically. Valid for circuits
composed of:
  - classical reversible gates: X, CNOT, Toffoli (CCX), SWAP
  - diagonal sign gates:        Z, CZ, CCZ

A basis-state simulator over random input samples: it never touches a 2^n
amplitude vector, so it validates 60+ qubit subroutines in milliseconds
(design doc section 2.3, row 1 of the evaluator table).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class KickmixState:
    """One computational basis state with an integer phase exponent.

    amplitude = i**phase  (phase in Z_4), x = integer bitstring
    """

    n: int
    x: int
    phase: int = 0
    ancilla_bits: frozenset = frozenset()  # indices that must end at |0>

    def bit(self, i: int) -> int:
        return (self.x >> i) & 1

    def flip(self, i: int) -> None:
        self.x ^= 1 << i

    def kick(self, exp: int) -> None:
        """Multiply amplitude by i**exp (exp in Z_4)."""
        self.phase = (self.phase + exp) & 3


class KickmixSimulator:
    """Evaluates reversible/phase circuits on sampled basis states."""

    def sample_state(self, n: int, ancilla_bits=(), rng=None) -> KickmixState:
        import random

        rng = rng or random
        return KickmixState(n=n, x=rng.getrandbits(n), ancilla_bits=frozenset(ancilla_bits))

    def run(self, circuit, state: KickmixState) -> KickmixState:
        for gate in circuit.gates:
            gate.apply_kickmix(state)
        return state

    def verify_oracle(
        self,
        circuit,
        truth_table: dict[int, int],
        ancilla_bits=(),
        n_samples: int = 128,
        seed: int = 0,
    ) -> bool:
        """Check V|x>|a> = |pi(x)>|a> (identity permutation, clean ancilla)."""
        import random

        rng = random.Random(seed)
        n = circuit.n
        for _ in range(n_samples):
            st = self.sample_state(n, ancilla_bits, rng)
            x_in = st.x
            st = self.run(circuit, st)
            if st.phase != 0:
                return False
            if st.x != truth_table.get(x_in, x_in):
                return False
        return True


class Gate:
    """Base gate with a statevector implementation.

    Subclasses with `reversible_classical = True` map basis states to basis
    states; subclasses with `is_diagonal = True` only apply phases. Both
    families keep single basis inputs single-support, which is what the
    kickmix fast path exploits.
    """

    name = "Gate"
    reversible_classical = False
    is_diagonal = False

    def targets(self) -> tuple[int, ...]:
        return ()

    def apply_kickmix(self, st: KickmixState) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def apply_statevector(self, psi: list[complex], n: int) -> None:
        raise NotImplementedError


class X(Gate):
    name = "X"
    reversible_classical = True

    def __init__(self, i: int):
        self.i = i

    def targets(self):
        return (self.i,)

    def apply_kickmix(self, st):
        st.flip(self.i)

    def apply_statevector(self, psi, n):
        step = 1 << self.i
        for base in range(0, len(psi), step * 2):
            for off in range(step):
                a, b = base + off, base + off + step
                psi[a], psi[b] = psi[b], psi[a]


class Z(Gate):
    name = "Z"
    is_diagonal = True

    def __init__(self, i: int):
        self.i = i

    def targets(self):
        return (self.i,)

    def apply_kickmix(self, st):
        if st.bit(self.i):
            st.kick(2)  # i**2 = -1

    def apply_statevector(self, psi, n):
        step = 1 << self.i
        for base in range(0, len(psi), step * 2):
            for off in range(step):
                psi[base + off + step] *= -1


class CNOT(Gate):
    name = "CNOT"
    reversible_classical = True

    def __init__(self, control: int, target: int):
        self.c, self.t = control, target

    def targets(self):
        return (self.c, self.t)

    def apply_kickmix(self, st):
        if st.bit(self.c):
            st.flip(self.t)

    def apply_statevector(self, psi, n):
        step = 1 << self.t
        for base in range(0, len(psi), step * 2):
            for off in range(step):
                if (base + off) & (1 << self.c):
                    a, b = base + off, base + off + step
                    psi[a], psi[b] = psi[b], psi[a]


class CZ(Gate):
    name = "CZ"
    is_diagonal = True

    def __init__(self, control: int, target: int):
        self.c, self.t = control, target

    def targets(self):
        return (self.c, self.t)

    def apply_kickmix(self, st):
        if st.bit(self.c) and st.bit(self.t):
            st.kick(2)

    def apply_statevector(self, psi, n):
        step = 1 << self.t
        for base in range(0, len(psi), step * 2):
            for off in range(step):
                idx = base + off + step
                if idx & (1 << self.c):
                    psi[idx] *= -1


class Toffoli(Gate):
    name = "CCX"
    reversible_classical = True

    def __init__(self, c1: int, c2: int, target: int):
        self.c1, self.c2, self.t = c1, c2, target

    def targets(self):
        return (self.c1, self.c2, self.t)

    def apply_kickmix(self, st):
        if st.bit(self.c1) and st.bit(self.c2):
            st.flip(self.t)

    def apply_statevector(self, psi, n):
        step = 1 << self.t
        mask = (1 << self.c1) | (1 << self.c2)
        for base in range(0, len(psi), step * 2):
            for off in range(step):
                if (base + off) & mask == mask:
                    a, b = base + off, base + off + step
                    psi[a], psi[b] = psi[b], psi[a]


class CCZ(Gate):
    name = "CCZ"
    is_diagonal = True

    def __init__(self, c1: int, c2: int, c3: int):
        self.c1, self.c2, self.c3 = c1, c2, c3

    def targets(self):
        return (self.c1, self.c2, self.c3)

    def apply_kickmix(self, st):
        if st.bit(self.c1) and st.bit(self.c2) and st.bit(self.c3):
            st.kick(2)

    def apply_statevector(self, psi, n):
        mask = (1 << self.c1) | (1 << self.c2) | (1 << self.c3)
        for idx in range(len(psi)):
            if idx & mask == mask:
                psi[idx] *= -1


class SWAP(Gate):
    name = "SWAP"
    reversible_classical = True

    def __init__(self, a: int, b: int):
        self.a, self.b = a, b

    def targets(self):
        return (self.a, self.b)

    def apply_kickmix(self, st):
        ba, bb = st.bit(self.a), st.bit(self.b)
        if ba != bb:
            st.flip(self.a)
            st.flip(self.b)

    def apply_statevector(self, psi, n):
        out = [0j] * len(psi)
        for idx, amp in enumerate(psi):
            ba = (idx >> self.a) & 1
            bb = (idx >> self.b) & 1
            j = idx
            if ba != bb:
                j = idx ^ (1 << self.a) ^ (1 << self.b)
            out[j] = amp
        psi[:] = out


def allows_fast_path(circuit) -> bool:
    """True if every gate is classical-reversible or diagonal, so basis
    inputs stay single-support and the kickmix evaluator is exact."""
    return all(g.reversible_classical or g.is_diagonal for g in circuit.gates)


@dataclass
class Circuit:
    """Ordered gate list over n qubits."""

    n: int
    gates: list = field(default_factory=list)

    def toffoli_count(self) -> int:
        return sum(1 for g in self.gates if isinstance(g, (Toffoli, CCZ)))

    def gate_count(self) -> int:
        return len(self.gates)

    def depth(self) -> int:
        """Greedy ASAP scheduling depth (each gate waits for its qubits)."""
        t = [0] * self.n
        for g in self.gates:
            start = max((t[i] for i in g.targets()), default=0)
            end = start + 1
            for i in g.targets():
                t[i] = end
        return max(t) if self.n else 0

    def peak_qubits(self) -> int:
        return self.n  # logical width; ancilla reuse lowers this

    def copy(self) -> "Circuit":
        return Circuit(self.n, list(self.gates))
