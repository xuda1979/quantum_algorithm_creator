"""Comprehensive test suite for the Quantum Algorithm Creator reference implementation.

Covers every layer of AGENT_DESIGN.md:
  Layer 1 triage rules (reject / necessity / opportunity / known-only)
  Layer 3 sandbox: kickmix exactness vs statevector, unitary fidelity,
                   semantic verification (positive & negative controls),
                   ancilla-cleanliness, scaling-fit guardrail
  Layer 2A library: known generators verify semantically on all sizes
  Layer 2B discovery: MAP-Elites archive, necessity & opportunity modes,
                   budget-boxing, negative controls rejected
  Layer 4 end-to-end: portfolio ranking, provenance, determinism
  Design invariants: parameterization, polynomial scaling, invention value
"""

import math

import pytest

from qac.kickmix import (
    Circuit, CNOT, CCZ, SWAP, Toffoli, X, Z,
    KickmixSimulator, KickmixState,
)
from qac.sandbox import (
    run_statevector, unitary_matrix, fidelity,
    check_ancilla_clean, check_semantics, fit_scaling, evaluate_candidate,
)
from qac.library import (
    KnownAlgorithmLibrary, Candidate,
    build_increment_ancilla_reference, increment_spec,
    increment_candidate_ancilla, bit_reversal_candidate,
    build_bit_reversal, bit_reversal_spec,
)
from qac.track_b import (
    MutatorStandIn, MAPelitesArchive, run_discovery, generator_to_candidate,
    GENERATOR_POOL, _lean_interleaved_increment,
    _zigzag_increment, _cyclic_increment, _inplace_lookahead_increment,
)
from qac.problem import IRKind, ProblemIR, triage
from qac.portfolio import run_agent


# ===========================================================================
# Layer 3 — kickmix simulator: exactness against the statevector evaluator
# ===========================================================================

class TestKickmixExactness:
    def _random_circuit(self, n, rng, length=40):
        c = Circuit(n)
        for _ in range(length):
            g = rng.randrange(5)
            i, j = rng.randrange(n), rng.randrange(n)
            if g == 0:
                c.gates.append(X(i))
            elif g == 1:
                c.gates.append(CNOT(i, j) if i != j else X(i))
            elif g == 2:
                if n >= 3:
                    idxs = rng.sample(range(n), 3)  # 3 distinct: valid Toffoli
                    c.gates.append(Toffoli(*idxs))
                else:
                    c.gates.append(X(i))
            elif g == 3:
                c.gates.append(SWAP(i, j) if i != j else X(i))
            else:
                c.gates.append(Z(i))
        return c

    def test_reversible_circuits_agree_with_statevector(self):
        import random

        rng = random.Random(42)
        for trial in range(20):
            n = rng.randrange(2, 6)
            c = self._random_circuit(n, rng)
            for x in range(1 << n):
                st = KickmixState(n=n, x=x)
                for g in c.gates:
                    g.apply_kickmix(st)
                psi = [0j] * (1 << n)
                psi[x] = 1.0
                for g in c.gates:
                    g.apply_statevector(psi, n)
                support = [idx for idx, a in enumerate(psi) if abs(a) > 1e-9]
                if not any(isinstance(g, Z) for g in c.gates):
                    assert len(support) == 1, "classical-reversible circuit must be basis-preserving"
                if len(support) != 1:
                    continue  # Z-mixed random circuit may superpose; kickmix invalid there
                assert support[0] == st.x
                ang = math.atan2(psi[support[0]].imag, psi[support[0]].real)
                expected_phase = int(round((ang / (math.pi / 2)) % 4))
                assert st.phase == expected_phase

    def test_phase_kickback_matches(self):
        st = KickmixState(n=2, x=0b10)
        Z(1).apply_kickmix(st)
        assert st.phase == 2
        Z(0).apply_kickmix(st)
        assert st.phase == 2

    def test_ccz_kicks_only_on_111(self):
        for x in range(8):
            st = KickmixState(n=3, x=x)
            CCZ(0, 1, 2).apply_kickmix(st)
            assert st.phase == (2 if x == 0b111 else 0)

    def test_toffoli_truth_table(self):
        for c1 in range(2):
            for c2 in range(2):
                st = KickmixState(n=3, x=c1 | (c2 << 1))
                Toffoli(0, 1, 2).apply_kickmix(st)
                assert st.x == ((c1 & c2) << 2) | c1 | (c2 << 1)

    def test_swap_identity_and_transposition(self):
        st = KickmixState(n=2, x=0b01)
        SWAP(0, 1).apply_kickmix(st)
        assert st.x == 0b10
        SWAP(0, 1).apply_kickmix(st)
        assert st.x == 0b01

    def test_sampling_deterministic_given_seed(self):
        import random

        s = KickmixSimulator()
        rng1, rng2 = random.Random(3), random.Random(3)
        a = [s.sample_state(5, rng=rng1).x for _ in range(10)]
        b = [s.sample_state(5, rng=rng2).x for _ in range(10)]
        assert a == b


# ===========================================================================
# Layer 3 — statevector / unitary machinery
# ===========================================================================

class TestStatevector:
    def test_identity(self):
        psi = run_statevector(Circuit(3))
        assert abs(psi[0] - 1.0) < 1e-12
        assert sum(abs(p) for p in psi[1:]) < 1e-12

    def test_x_flips_bit0(self):
        psi = run_statevector(Circuit(1, [X(0)]))
        assert abs(psi[1] - 1.0) < 1e-12

    def test_unitary_of_circuit_is_unitary(self):
        for n in (2, 3):
            c = Circuit(n, [Toffoli(0, 1, n - 1) if n >= 3 else CNOT(0, 1), Z(0), SWAP(0, n - 1)])
            u = unitary_matrix(c)
            dim = 1 << n
            for r in range(dim):
                for cc in range(dim):
                    s = sum(u[k][r].conjugate() * u[k][cc] for k in range(dim))
                    expect = 1.0 if r == cc else 0.0
                    assert abs(s - expect) < 1e-9

    def test_fidelity_of_circuit_with_itself_is_one(self):
        c = Circuit(3, [CNOT(0, 1), Z(2), Toffoli(0, 1, 2)])
        assert fidelity(c, c.copy()) == pytest.approx(1.0, abs=1e-12)

    def test_fidelity_detects_difference(self):
        a = Circuit(2, [CNOT(0, 1)])
        b = Circuit(2, [X(0), CNOT(0, 1), X(0)])
        assert fidelity(a, b) < 0.999


# ===========================================================================
# Layer 3 — semantic verification & ancilla cleanliness (Maker-Checker core)
# ===========================================================================

class TestSemanticVerification:
    def test_library_reference_passes_semantics(self):
        for n in (3, 4, 5):
            c = build_increment_ancilla_reference(n)
            rep = check_semantics(c, increment_spec(n), n)
            assert rep.ok, rep.reasons

    def test_invented_lean_passes_semantics(self):
        for n in (3, 4, 5):
            c = _lean_interleaved_increment(n)
            rep = check_semantics(c, increment_spec(n), n)
            assert rep.ok, rep.reasons

    def test_wrong_function_is_caught(self):
        # increment vs decrement: must fail
        c = build_increment_ancilla_reference(3)
        wrong_spec = {x: (x - 1) % 8 for x in range(8)}
        rep = check_semantics(c, wrong_spec, 3)
        assert not rep.ok

    def test_dirty_ancilla_is_caught(self):
        # ancilla keeps a copy of b_0: violates uncomputation invariant
        def build(n):
            return Circuit(n + 1, [CNOT(0, n)])

        rep = check_semantics(build(2), {x: x for x in range(4)}, 2)
        assert not rep.ok
        assert any("ancilla" in r or "amplitudes" in r for r in rep.reasons)

    def test_stray_phase_is_caught(self):
        # correct permutation but a stray Z on data: amplitude != +1
        c = Circuit(2, [CNOT(0, 1), X(0), Z(1)])
        rep = check_semantics(c, increment_spec(2), 2)
        assert not rep.ok

    def test_negative_control_mutations_fail_semantics(self):
        for gen in (_zigzag_increment, _cyclic_increment, _inplace_lookahead_increment):
            bad = False
            for n in (3, 4, 5):
                rep = check_semantics(gen(n), increment_spec(n), n)
                if not rep.ok:
                    bad = True
                    break
            assert bad, f"{gen.__name__} should be rejected by the sandbox"

    def test_ancilla_clean_report(self):
        rep = check_ancilla_clean(build_increment_ancilla_reference, 3, [3, 4])
        assert rep.clean
        assert rep.entangled_indices == []


# ===========================================================================
# Layer 3 — scaling-fit guardrail
# ===========================================================================

class TestScalingFit:
    def test_polynomial_detected(self):
        fit = fit_scaling({3: 6, 4: 8, 5: 10, 6: 12, 7: 14})
        assert fit["verdict"] == "polynomial"
        assert fit["poly_r2"] > 0.99

    def test_exponential_detected(self):
        fit = fit_scaling({3: 8, 4: 16, 5: 32, 6: 64, 7: 128})
        assert fit["verdict"] == "exponential"

    def test_quadratic_poly_detected(self):
        fit = fit_scaling({3: 9, 4: 16, 5: 25, 6: 36, 7: 49})
        assert fit["verdict"] == "polynomial"

    def test_insufficient_data(self):
        assert fit_scaling({3: 5, 4: 6})["verdict"] == "insufficient_data"


# ===========================================================================
# Layer 3 — the evaluation contract (evaluate_candidate)
# ===========================================================================

class TestEvaluateCandidate:
    def test_known_candidate_passes(self):
        rep = evaluate_candidate(increment_candidate_ancilla(), sizes=(3, 4, 5))
        assert rep.passed, rep.failure_reasons
        assert all(rep.correctness.values())
        assert all(rep.ancilla_clean.values())

    def test_invented_candidate_passes(self):
        cand = generator_to_candidate(_lean_interleaved_increment)
        rep = evaluate_candidate(cand, sizes=(3, 4, 5))
        assert rep.passed, rep.failure_reasons

    def test_broken_candidate_fails_with_reasons(self):
        def build(n):
            return build_increment_ancilla_reference(n)

        cand = Candidate("test:broken", "A", build, lambda n: {x: x for x in range(1 << n)}, lambda n: n, lambda n: [])
        rep = evaluate_candidate(cand, sizes=(3, 4, 5))
        assert not rep.passed
        assert rep.failure_reasons

    def test_width_mismatch_detected(self):
        cand = Candidate(
            "test:width", "A",
            lambda n: Circuit(3),
            lambda n: increment_spec(n),
            lambda n: n,
            lambda n: [],
        )
        rep = evaluate_candidate(cand, sizes=(4,))
        assert not rep.passed
        assert any("width mismatch" in r for r in rep.failure_reasons)

    def test_report_json_serializable_and_fast(self):
        import json
        import time

        t0 = time.perf_counter()
        rep = evaluate_candidate(increment_candidate_ancilla(), sizes=(3, 4, 5))
        dt = time.perf_counter() - t0
        blob = json.dumps(rep.to_dict())
        assert "correctness" in blob
        assert dt < 2.0, "evaluation must stay sub-second-ish (Phase-1 requirement)"

    def test_deterministic_across_calls(self):
        r1 = evaluate_candidate(increment_candidate_ancilla(), sizes=(3, 4, 5)).to_dict()
        r2 = evaluate_candidate(increment_candidate_ancilla(), sizes=(3, 4, 5)).to_dict()
        r1.pop("wall_time_s"), r2.pop("wall_time_s")
        assert r1 == r2


# ===========================================================================
# Layer 2A — known-algorithm library
# ===========================================================================

class TestLibrary:
    def test_every_known_entry_verifies(self):
        lib = KnownAlgorithmLibrary()
        reports = lib.verify_all(sizes=(3, 4, 5))
        assert reports
        for key, rep in reports.items():
            assert rep.passed, f"{key}: {rep.failure_reasons}"

    def test_reference_actually_increments(self):
        for n in (3, 4, 5):
            c = build_increment_ancilla_reference(n)
            for x in range(1 << n):
                st = KickmixState(n=c.n, x=x)
                for g in c.gates:
                    g.apply_kickmix(st)
                assert st.x & ((1 << n) - 1) == (x + 1) % (1 << n)
                assert st.x >> n == 0, "ancillas must uncompute"

    def test_bit_reversal_semantics(self):
        for n in (3, 4, 5):
            rep = check_semantics(build_bit_reversal(n), bit_reversal_spec(n), n)
            assert rep.ok

    def test_missing_key_raises(self):
        lib = KnownAlgorithmLibrary()
        with pytest.raises(KeyError):
            lib.synthesize("shors_factoring", 5)


# ===========================================================================
# Layer 1 — triage gate
# ===========================================================================

class TestTriage:
    def test_qram_quadratic_rejected(self):
        ir = ProblemIR(IRKind.ORACLE_SEARCH, known_template="grover", data_loading="qram")
        tr = triage(ir, quantum_speedup_poly=2)
        assert not tr.recommend_quantum
        assert tr.track_b_mode is None

    def test_no_known_template_necessity_mode(self):
        ir = ProblemIR(IRKind.ARITHMETIC, known_template=None)
        tr = triage(ir)
        assert tr.recommend_quantum
        assert tr.track_a is False
        assert tr.track_b_mode == "necessity"

    def test_bottleneck_opportunity_mode(self):
        ir = ProblemIR(IRKind.ARITHMETIC, known_template="increment", notes={"resource_bottleneck": True})
        tr = triage(ir)
        assert tr.track_a and tr.track_b_mode == "opportunity"

    def test_plain_known_track_a_only(self):
        ir = ProblemIR(IRKind.LINEAR_SYSTEM, known_template="hhl")
        tr = triage(ir)
        assert tr.track_a and tr.track_b_mode is None


# ===========================================================================
# Layer 2B — discovery engine
# ===========================================================================

class TestDiscovery:
    def test_all_generator_variants_semantically_checked(self):
        # positive control passes
        assert check_semantics(_lean_interleaved_increment(5), increment_spec(5), 5).ok
        # negative controls fail
        for gen in (_zigzag_increment, _cyclic_increment, _inplace_lookahead_increment):
            assert not check_semantics(gen(5), increment_spec(5), 5).ok

    def test_necessity_mode_succeeds_with_inventions(self):
        res = run_discovery(sizes=(3, 4, 5), mode="necessity", seed=0, max_batches=2)
        assert res.attempts >= 1
        assert res.succeeded
        assert len(res.invented) >= 1
        for e in res.invented:
            assert e.report.passed

    def test_opportunity_mode_requires_pareto_vs_baseline(self):
        res = run_discovery(sizes=(3, 4, 5), mode="opportunity", seed=0, max_batches=2)
        assert res.succeeded, "lean incrementer must Pareto-dominate the ancilla reference"
        best = res.invented[0]
        assert best.width_max < 2 * 5 - 1  # narrower than library's 9 qubits

    def test_budget_boxing_limits_attempts(self):
        res = run_discovery(sizes=(3, 4, 5), max_batches=1, mode="necessity", seed=0)
        assert res.attempts <= len(GENERATOR_POOL)

    def test_archive_insertion_and_dominance(self):
        arch = MAPelitesArchive()
        cand = generator_to_candidate(_lean_interleaved_increment)
        rep = evaluate_candidate(cand, sizes=(3, 4, 5))
        assert arch.try_insert(cand, rep)
        assert not arch.try_insert(cand, rep)  # dominated by itself
        assert len(arch.all_passed()) == 1
        assert arch.best() is not None

    def test_failed_candidates_never_enter_archive(self):
        arch = MAPelitesArchive()
        cand = generator_to_candidate(_zigzag_increment)  # negative control
        rep = evaluate_candidate(cand, sizes=(3, 4, 5))
        assert not rep.passed
        assert not arch.try_insert(cand, rep)
        assert arch.all_passed() == []

    def test_feedback_trace_present(self):
        res = run_discovery(sizes=(3, 4, 5), mode="necessity", seed=0)
        assert res.trace, "diagnostic trace must feed back into the mutator context"
        assert any("FAIL" in t or "PASS" in t for t in res.trace)


# ===========================================================================
# Layer 4 — end-to-end agent runs
# ===========================================================================

class TestEndToEnd:
    def test_necessity_run_delivers_invented_portfolio(self):
        ir = ProblemIR(IRKind.ARITHMETIC, known_template=None)
        res = run_agent("reversible incrementer", ir, sizes=(3, 4, 5), seed=7)
        assert res.delivered
        assert all(e.provenance == "invented" for e in res.portfolio)
        assert res.triage.track_b_mode == "necessity"

    def test_opportunity_run_mixed_portfolio(self):
        ir = ProblemIR(
            IRKind.ARITHMETIC, known_template="increment",
            notes={"library_key": "increment", "resource_bottleneck": True},
        )
        res = run_agent("incrementer with bottleneck", ir, sizes=(3, 4, 5), seed=7)
        assert res.delivered
        provs = {e.provenance for e in res.portfolio}
        assert "known" in provs and "invented" in provs  # one problem, many solutions

    def test_known_only_run(self):
        ir = ProblemIR(IRKind.LINEAR_SYSTEM, known_template="hhl", notes={"library_key": "increment"})
        res = run_agent("linear solve", ir, sizes=(3, 4, 5))
        assert res.delivered
        assert all(e.provenance == "known" for e in res.portfolio)
        assert res.triage.track_b_mode is None

    def test_rejected_problem_empty_portfolio_with_reason(self):
        ir = ProblemIR(IRKind.ORACLE_SEARCH, known_template="grover", data_loading="qram")
        res = run_agent("search", ir, sizes=(3,), quantum_speedup_poly=2)
        assert not res.delivered
        assert not res.triage.recommend_quantum
        assert res.notes

    def test_portfolio_is_ranked_by_resources(self):
        ir = ProblemIR(
            IRKind.ARITHMETIC, known_template="increment",
            notes={"library_key": "increment", "resource_bottleneck": True},
        )
        res = run_agent("rank check", ir, sizes=(3, 4, 5), seed=7)
        scores = [e.score for e in res.portfolio]
        assert scores == sorted(scores)

    def test_full_pipeline_deterministic(self):
        ir = ProblemIR(IRKind.ARITHMETIC, known_template=None)
        a = run_agent("det", ir, sizes=(3, 4, 5), seed=7)
        b = run_agent("det", ir, sizes=(3, 4, 5), seed=7)
        assert [e.candidate_id for e in a.portfolio] == [e.candidate_id for e in b.portfolio]

    def test_every_delivered_entry_passed_the_sandbox(self):
        ir = ProblemIR(
            IRKind.ARITHMETIC, known_template="increment",
            notes={"library_key": "increment", "resource_bottleneck": True},
        )
        res = run_agent("verify all", ir, sizes=(3, 4, 5), seed=7)
        for e in res.portfolio:
            assert e.report.passed
            assert all(e.report.correctness.values())

    def test_invented_beats_known_in_portfolio(self):
        """The design's promise: opportunity mode yields a Pareto-better invention."""
        ir = ProblemIR(
            IRKind.ARITHMETIC, known_template="increment",
            notes={"library_key": "increment", "resource_bottleneck": True},
        )
        res = run_agent("invention value", ir, sizes=(3, 4, 5), seed=7)
        known = [e for e in res.portfolio if e.provenance == "known"]
        invented = [e for e in res.portfolio if e.provenance == "invented"]
        assert known and invented
        k, v = known[0], invented[0]
        m = k.report.sizes[-1]
        assert v.report.peak_qubits[m] < k.report.peak_qubits[m], "invention must save qubits"
        assert v.report.toffoli[m] <= k.report.toffoli[m], "at no Toffoli cost"


# ===========================================================================
# Cross-layer design invariants
# ===========================================================================

class TestDesignInvariants:
    def test_candidates_are_parameterized_never_static(self):
        cand = generator_to_candidate(_lean_interleaved_increment)
        assert cand.build(3).n == 4  # 2n-2
        assert cand.build(5).n == 8
        assert cand.build(4).n != cand.build(5).n, "generators must take n as an argument (design 2.1)"

    def test_reference_scaling_is_polynomial(self):
        counts = {n: build_increment_ancilla_reference(n).toffoli_count() for n in (3, 4, 5, 6, 7)}
        assert fit_scaling(counts)["verdict"] == "polynomial"

    def test_invented_scaling_is_polynomial(self):
        counts = {n: _lean_interleaved_increment(n).toffoli_count() for n in (3, 4, 5, 6, 7)}
        assert fit_scaling(counts)["verdict"] == "polynomial"

    def test_invention_pareto_dominates_at_every_size(self):
        for n in (3, 4, 5, 6):
            ref = build_increment_ancilla_reference(n)
            inv = _lean_interleaved_increment(n)
            assert inv.n < ref.n, f"n={n}: invented width {inv.n} must beat {ref.n}"
            assert inv.toffoli_count() <= ref.toffoli_count()
            assert inv.gate_count() <= ref.gate_count()

    def test_demo_cli_runs(self):
        from qac import cli

        cli.demo_increment()
        cli.demo_opportunity()
        cli.demo_reject()
