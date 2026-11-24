"""Independently check the reported circuits and compare a common Qiskit basis."""

import numpy as np
import qiskit
from qiskit import QuantumCircuit, transpile
from qiskit.circuit.library import IntegerComparator, WeightedAdder
from qiskit.quantum_info import Statevector

from qac.library import KnownAlgorithmLibrary, lessthan_k, majority_K, weighted_theta
from qac.sota_baselines import specialized_baselines
from qac.track_b import _lean_interleaved_increment, _topwrite_increment, generator_to_candidate, run_discovery


def to_qiskit(circuit):
    qc = QuantumCircuit(circuit.n)
    methods = {"CNOT": qc.cx, "CCX": qc.ccx, "X": qc.x, "Z": qc.z,
               "CZ": qc.cz, "CCZ": qc.ccz, "SWAP": qc.swap}
    for gate in circuit.gates:
        methods[gate.name](*gate.targets())
    return qc


def qiskit_reference(family, n):
    if family == "less_than":
        return IntegerComparator(n - 1, lessthan_k(n), geq=False)
    if family == "majority":
        weights, threshold = [1] * n, majority_K(n)
    else:
        weights, threshold = list(range(1, n + 1)), weighted_theta(n)
    add = WeightedAdder(n, weights)
    compare = IntegerComparator(add.num_sum_qubits, threshold)
    accumulator = list(range(n + 1, n + 1 + add.num_sum_qubits))
    scratch_start = n + 1 + len(accumulator)
    width = scratch_start + max(add.num_ancillas, compare.num_ancillas)
    scratch = list(range(scratch_start, width))
    qc = QuantumCircuit(width)
    add_wires = list(range(n)) + accumulator + scratch[:add.num_ancillas]
    qc.compose(add, add_wires, inplace=True)
    qc.compose(compare, accumulator + [n] + scratch[:compare.num_ancillas], inplace=True)
    qc.compose(add.inverse(), add_wires, inplace=True)
    return qc


def check(candidate, qc, n, rng):
    inputs = 1 << candidate.n_data(n)
    spec = candidate.spec(n)
    initial = np.zeros(1 << qc.num_qubits, dtype=complex)
    values = rng.normal(size=inputs) + 1j * rng.normal(size=inputs)
    initial[:inputs] = values / np.linalg.norm(values)
    expected = np.zeros_like(initial)
    expected[[spec[x] for x in range(inputs)]] = initial[:inputs]
    actual = Statevector(initial).evolve(qc).data
    np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-12)
    return float(np.max(np.abs(actual - expected)))


def print_resources(candidate_id, qc):
    compiled = transpile(qc, basis_gates=["cx", "rz", "sx", "x"],
                         optimization_level=3, seed_transpiler=21)
    print(candidate_id, compiled.num_qubits, compiled.count_ops().get("cx", 0),
          compiled.size(), compiled.depth(), sep="\t")


def main():
    print(f"Qiskit {qiskit.__version__}")
    rng = np.random.default_rng(237)
    for generator in (_lean_interleaved_increment, _topwrite_increment):
        candidate = generator_to_candidate(generator)
        errors = [check(candidate, to_qiskit(candidate.build(n)), n, rng) for n in (3, 5, 7)]
        print(f"{candidate.id} max amplitude error = {max(errors):.3g}")

    library = KnownAlgorithmLibrary()
    specialized = specialized_baselines(library)
    print("n=10 basis counts\twidth\tCX\tgates\tdepth")
    for family in ("majority", "weighted_threshold", "less_than"):
        result = run_discovery(problem_key=family, seed=7, holdout_sizes=(6, 7, 8, 10))
        if not result.certified:
            raise RuntimeError(f"no verified candidates for {family}")
        reference = library.synthesize(family, 10)
        comparisons = [reference]
        if family in specialized:
            comparisons.append(specialized[family])
        for baseline in comparisons:
            print_resources(baseline.id, to_qiskit(baseline.build(10)))
        for n in (3, 5):
            check(reference, qiskit_reference(family, n), n, rng)
        print_resources(f"qiskit:{family}", qiskit_reference(family, 10))
        for entry in result.certified:
            candidate = entry.candidate
            errors = [check(candidate, to_qiskit(candidate.build(n)), n, rng) for n in (3, 5, 7)]
            print(f"{candidate.id} max amplitude error = {max(errors):.3g}")
            print_resources(candidate.id, to_qiskit(candidate.build(10)))


if __name__ == "__main__":
    main()
