"""Regressions for semantic verification and the evolutionary search path."""

from random import Random

import pytest

from qac.arithmetic import SumProgram, build_sum_threshold
from qac.kickmix import CCZ, CNOT, Circuit, KickmixSimulator, KickmixState, X, Z
from qac.library import Candidate, KnownAlgorithmLibrary, increment_spec
from qac.novelty import _fingerprint, _same_spec, certify_innovation
from qac.portfolio import run_agent
from qac.problem import IRKind, ProblemIR
from qac.sandbox import evaluate_candidate, fit_scaling
from qac.track_b import run_discovery


def test_kickmix_samples_zero_ancillas_and_rejects_incomplete_spec():
    sim = KickmixSimulator()
    assert sim.verify_oracle(Circuit(2, [X(0)]), {0: 1, 1: 0}, ancilla_bits=[1])
    assert not sim.verify_oracle(Circuit(2, [X(1)]), {0: 0, 1: 1}, ancilla_bits=[1])
    assert not sim.verify_oracle(Circuit(2), {}, ancilla_bits=[1])


@pytest.mark.parametrize("gate,reason", [(CNOT(0, 0), "repeated operands"),
                                         (CNOT(0, 2), "out-of-range operand")])
def test_malformed_gates_cannot_pass_evaluation(gate, reason):
    cand = Candidate("bad-gate", "B", lambda n: Circuit(2, [gate]), lambda n: lambda x: x, lambda n: 2)
    report = evaluate_candidate(cand, sizes=(2,))
    assert not report.passed and reason in report.failure_reasons[0]


def test_false_ancilla_layout_fails_even_for_identity():
    cand = Candidate("bad-layout", "B", lambda n: Circuit(3), lambda n: lambda x: x,
                     lambda n: 2, lambda n: [0])
    report = evaluate_candidate(cand, sizes=(2,))
    assert not report.passed and "ancillas" in report.failure_reasons[0]


@pytest.mark.parametrize("gate,reason", [(Z(0), "stray phase"), (X(1), "ancilla bits nonzero")])
def test_packed_verifier_checks_phase_and_ancillas(gate, reason):
    cand = Candidate("bad-semantics", "B", lambda n: Circuit(2, [gate]), lambda n: lambda x: x,
                     lambda n: 1, lambda n: [1])
    report = evaluate_candidate(cand, sizes=(1,))
    assert report.semantic_backend == {1: "packed"}
    assert report.ancilla_clean[1] is isinstance(gate, Z)
    assert not report.passed and reason in report.failure_reasons[0]


def test_scaling_guardrail_counts_total_gates_even_without_toffolis():
    cand = Candidate("exponential", "B", lambda n: Circuit(1, [X(0)] * (1 << n)),
                     lambda n: lambda x: x, lambda n: 1)
    report = evaluate_candidate(cand, sizes=range(1, 7))
    assert all(report.correctness.values())
    assert not report.passed and "exponential gates" in report.failure_reasons[-1]


def test_scaling_fit_does_not_reject_slow_growth_on_a_short_window():
    assert fit_scaling({9: 93, 11: 119, 12: 135, 13: 153})["verdict"] == "inconclusive"


def test_fingerprint_normalizes_ccz_and_disjoint_gate_order():
    first = Circuit(4, [CCZ(0, 1, 2), X(3)])
    second = Circuit(4, [X(3), CCZ(2, 0, 1)])
    assert _fingerprint(first) == _fingerprint(second)
    assert _fingerprint(first) != _fingerprint(Circuit(5, first.gates))


def test_same_contract_compares_callable_and_mapping_on_requested_sizes():
    mapping = Candidate("mapping", "A", Circuit, increment_spec, lambda n: n)
    callable_spec = Candidate("callable", "A", Circuit, lambda n: lambda x: (x + 1) % (1 << n), lambda n: n)
    different = Candidate("different", "A", Circuit,
                          lambda n: lambda x: (x + (1 if n < 6 else 2)) % (1 << n), lambda n: n)
    assert _same_spec(mapping, callable_spec, sizes=(2, 6))
    assert not _same_spec(mapping, different, sizes=(2, 6))


def test_certificate_fails_on_skipped_or_missing_size_and_without_baseline():
    cand = Candidate("wide-holdout", "B", lambda n: Circuit(1 if n < 6 else 129),
                     lambda n: lambda x: x, lambda n: 1,
                     lambda n: [] if n < 6 else list(range(1, 129)))
    report = evaluate_candidate(cand, sizes=(3, 4, 5))
    assert report.passed
    skipped = certify_innovation(cand, report, extended_sizes=(6,))
    assert not skipped.correct and "n=6" in skipped.reasons[0]
    empty = certify_innovation(cand, report, extended_sizes=())
    assert not empty.correct
    unbenchmarked = certify_innovation(cand, report, baseline_candidates=[], extended_sizes=(5,))
    assert unbenchmarked.correct and not unbenchmarked.certified
    assert unbenchmarked.novelty_verdict == "unbenchmarked"


@pytest.mark.parametrize("ir", [ProblemIR(IRKind.LINEAR_SYSTEM, known_template="hhl", notes={"library_key": "increment"}),
                               ProblemIR(IRKind.ARITHMETIC),
                               ProblemIR(IRKind.ARITHMETIC, known_template="increment", notes={"library_key": "majority"})])
def test_agent_requires_a_matching_reversible_contract(ir):
    result = run_agent("unspecified or unrelated", ir)
    assert not result.delivered and result.discovery is None
    assert result.notes


@pytest.mark.parametrize("family,best_id,toffolis", [("majority", "optimized:majority:1-1-0-input-overflow", 58),
                                                     ("weighted_threshold", "optimized:weighted_threshold:1-1-0-low_first-ripple", 67),
                                                     ("less_than", "optimized:less_than:1-0", 11)])
def test_agent_mutates_verified_elites_and_recovers_held_out_performance(family, best_id, toffolis):
    result = run_discovery(problem_key=family, holdout_sizes=(6, 7, 8, 10), seed=7)
    assert 1 < result.attempts <= 32
    assert not set(result.search_sizes) & set(result.holdout_sizes)
    found = next(entry for entry in result.certified if entry.candidate.id == best_id)
    assert result.lineage[best_id]["parent"] in result.lineage
    cert = found.certificate
    assert cert.kickmix_sizes == cert.extended_sizes == [1, 2, 3, 4, 5, 6, 7, 8, 10]
    assert cert.resources[8]["toffoli"] == toffolis
    assert cert.literature_status == "unassessed"
    contract = KnownAlgorithmLibrary().synthesize(family, 8)
    assert _same_spec(found.candidate, contract, sizes=(8, 10))


def test_discovery_holdout_must_be_disjoint_and_initial_program_has_no_gain():
    with pytest.raises(ValueError, match="disjoint"):
        run_discovery(problem_key="majority", sizes=(3, 4, 5), holdout_sizes=(5, 6))
    result = run_discovery(problem_key="majority", evolve=False)
    assert result.attempts == 1
    assert not result.succeeded


def test_weighted_threshold_grammar_on_arbitrary_small_integer_weights():
    rng = Random(42)
    simulator = KickmixSimulator()
    for _ in range(25):
        weights = tuple(rng.randrange(0, 8) for _ in range(rng.randrange(1, 5)))
        threshold = rng.randrange(-1, sum(weights) + 2)
        n = len(weights) + 1
        flag = 1 << len(weights)

        def spec(x):
            total = sum(w for i, w in enumerate(weights) if (x >> i) & 1)
            return x ^ (flag if total >= threshold else 0)

        for marker in ("ripple", "polarity", "overflow"):
            program = SumProgram(bounded=True, fuse=True, pair=True, order="low_first", marker=marker)
            circuit = build_sum_threshold(weights, threshold, program)
            for x in range(1 << n):
                out = simulator.run(circuit, KickmixState(circuit.n, x))
                assert (out.x, out.phase) == (spec(x), 0), (weights, threshold, program, x)
