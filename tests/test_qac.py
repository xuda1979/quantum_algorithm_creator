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
    GENERATOR_POOL, _lean_interleaved_increment, _topwrite_increment,
    _zigzag_increment, _cyclic_increment, _inplace_lookahead_increment,
)
from qac.problem import IRKind, ProblemIR, triage
from qac.portfolio import run_agent
from qac.novelty import InnovationCertificate, certify_innovation, _same_spec, _resources
from qac.library import increment_candidate_ancilla as _inc_cand
from qac.sota_baselines import (
    sota_baselines, CUCCARO_CITATION, CITATION_SHA256,
    build_cuccaro_mod_adder, build_cuccaro_increment, build_cuccaro_addconst,
    mod_adder_spec, cuccaro_mod_adder_candidate, cuccaro_increment_candidate,
    cuccaro_addconst_candidate,
)


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
# Literature SOTA baselines (Cuccaro/CDKM, arXiv:quant-ph/0410184)
# ===========================================================================

class TestSotaBaselines:
    """The honest 'valuable' floor: cited literature constructions, verified
    as coded Candidates through the same sandbox as everything else."""

    def test_citation_hash_is_pinned(self):
        import hashlib
        assert hashlib.sha256(CUCCARO_CITATION.encode()).hexdigest() == CITATION_SHA256
        assert "arXiv:quant-ph/0410184" in CUCCARO_CITATION

    def test_every_sota_baseline_verifies_through_sandbox(self):
        for cand in sota_baselines():
            peak = cand.build(6).n
            rep = evaluate_candidate(cand, sizes=(2, 3, 4, 5, 6), max_total_width=max(12, peak))
            assert rep.passed, f"{cand.id}: {rep.failure_reasons}"
            assert all(rep.correctness.values()), cand.id
            assert all(rep.ancilla_clean.values()), cand.id

    def test_cuccaro_costs_are_pinned(self):
        # mod-adder / incrementer / addconst all pay the full CDKM price:
        # Toffoli 2n, width 2n+1 (paper section 5: constants don't simplify).
        for n in range(2, 7):
            for build in (build_cuccaro_mod_adder, build_cuccaro_increment, build_cuccaro_addconst):
                c = build(n)
                assert c.n == 2 * n + 1, (build.__name__, n)
                assert c.toffoli_count() == 2 * n, (build.__name__, n)
            assert build_cuccaro_mod_adder(n).gate_count() == 6 * n
            assert build_cuccaro_mod_adder(n).depth() == 5 * n + 1
            assert build_cuccaro_increment(n).gate_count() == 6 * n + 2
            assert build_cuccaro_increment(n).depth() == 5 * n + 2

    def test_cuccaro_constant_register_costs_more_than_a_specialized_incrementer(self):
        for n in (3, 4, 5, 6):
            sota = build_cuccaro_increment(n)
            known = build_increment_ancilla_reference(n)
            assert sota.n > known.n
            assert sota.toffoli_count() > known.toffoli_count()
            assert sota.gate_count() > known.gate_count()
            assert sota.depth() > known.depth()

    def test_mod_adder_spec_matches_exhaustive_truth_table(self):
        for n in (2, 3):
            spec = mod_adder_spec(n)
            assert len(spec) == 1 << (2 * n)
            for x, y in spec.items():
                a, b = x & ((1 << n) - 1), x >> n
                assert (y & ((1 << n) - 1)) == a  # a register preserved
                assert (y >> n) == (a + b) % (1 << n)  # b := a + b mod 2^n

    def test_sota_candidates_carry_candidate_protocol(self):
        for cand in sota_baselines():
            assert cand.track == "A"
            c5 = cand.build(5)
            assert c5.n == cand.n_data(5) + len(cand.ancilla(5))
            assert isinstance(cand.spec(5), dict)

    def test_innovation_gate_defaults_include_sota_baselines(self):
        cand = generator_to_candidate(_lean_interleaved_increment)
        rep = evaluate_candidate(cand, sizes=(3, 4, 5))
        cert = certify_innovation(cand, report=rep)
        assert "known:increment-ancilla" in cert.baselines_compared
        assert "sota:cdkm-increment" in cert.baselines_compared
        assert "sota:cdkm-increment" in cert.dominated_baselines
        assert "reference:shared-prefix-increment" in cert.baselines_compared
        assert cert.correct and not cert.certified

    def test_sota_same_spec_map_and_dominance_receipts(self):
        # Machine-checked same-spec map (novelty._same_spec, not hand-assigned):
        # exactly two library families are constrained by a CDKM baseline.
        # For the other ten, no SOTA baseline shares the spec, so the honest
        # gate leaves their certificates unchanged (first-solution value).
        lib = KnownAlgorithmLibrary()
        expected = {("increment", "sota:cdkm-increment"),
                    ("add_const", "sota:cdkm-addconst")}
        found = set()
        for key, fam in lib._entries.items():
            for s in sota_baselines():
                if _same_spec(fam, s):
                    found.add((key, s.id))
        assert found == expected, found
        # Both paired families strictly dominate their SOTA pair on ALL 4
        # axes at every size n=3..6 — the leave-one-out 'valuable' receipts.
        # Strict width inequality also rules out unitary equality, so the
        # 'novel' verdict against the SOTA pair is structural, not simulated.
        for key, sid in sorted(expected):
            fam = lib._entries[key]
            s = next(c for c in sota_baselines() if c.id == sid)
            for n in (3, 4, 5, 6):
                rf, rs = _resources(fam.build(n)), _resources(s.build(n))
                for axis in ("width", "toffoli", "gates", "depth"):
                    assert rf[axis] < rs[axis], (key, sid, n, axis, rf, rs)


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

    def test_necessity_mode_succeeds_with_optimizations(self):
        res = run_discovery(problem_key="less_than", mode="necessity", seed=0, max_batches=5)
        assert res.attempts >= 1
        assert res.succeeded
        assert len(res.invented) >= 1
        for e in res.invented:
            assert e.report.passed

    def test_opportunity_mode_requires_pareto_vs_baseline(self):
        res = run_discovery(problem_key="less_than", mode="opportunity", seed=0, max_batches=5)
        assert res.succeeded
        best = res.certified[0].certificate
        assert best.valuable and "known:lessthan-borrow" in best.dominated_baselines

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
    def test_necessity_run_delivers_optimized_portfolio(self):
        ir = ProblemIR(IRKind.ARITHMETIC, notes={"library_key": "less_than"})
        res = run_agent("coherent comparison", ir, sizes=(3, 4, 5), seed=7)
        assert res.delivered
        assert all(e.provenance == "optimized" for e in res.portfolio)
        assert res.triage.track_b_mode == "necessity"

    def test_opportunity_run_mixed_portfolio(self):
        ir = ProblemIR(
            IRKind.ARITHMETIC, known_template="less_than",
            notes={"library_key": "less_than", "resource_bottleneck": True},
        )
        res = run_agent("comparison with bottleneck", ir, sizes=(3, 4, 5), seed=7)
        assert res.delivered
        provs = {e.provenance for e in res.portfolio}
        assert "known" in provs and "optimized" in provs  # one problem, many solutions

    def test_known_only_run(self):
        ir = ProblemIR(IRKind.ARITHMETIC, known_template="bit_reversal")
        res = run_agent("bit reversal", ir, sizes=(3, 4, 5))
        assert res.delivered
        assert all(e.provenance == "known" for e in res.portfolio)
        assert res.triage.track_b_mode is None

    def test_rejected_problem_empty_portfolio_with_reason(self):
        ir = ProblemIR(IRKind.ORACLE_SEARCH, known_template="grover", data_loading="qram")
        res = run_agent("search", ir, sizes=(3,), quantum_speedup_poly=2)
        assert not res.delivered
        assert not res.triage.recommend_quantum
        assert res.triage.reasons

    def test_portfolio_is_ranked_by_resources(self):
        ir = ProblemIR(
            IRKind.ARITHMETIC, known_template="less_than",
            notes={"library_key": "less_than", "resource_bottleneck": True},
        )
        res = run_agent("rank check", ir, sizes=(3, 4, 5), seed=7)
        scores = [e.score for e in res.portfolio]
        assert scores == sorted(scores)

    def test_full_pipeline_deterministic(self):
        ir = ProblemIR(IRKind.ARITHMETIC, notes={"library_key": "less_than"})
        a = run_agent("det", ir, sizes=(3, 4, 5), seed=7)
        b = run_agent("det", ir, sizes=(3, 4, 5), seed=7)
        assert [e.candidate_id for e in a.portfolio] == [e.candidate_id for e in b.portfolio]
        assert a.discovery.lineage == b.discovery.lineage

    def test_every_delivered_entry_passed_the_sandbox(self):
        ir = ProblemIR(
            IRKind.ARITHMETIC, known_template="less_than",
            notes={"library_key": "less_than", "resource_bottleneck": True},
        )
        res = run_agent("verify all", ir, sizes=(3, 4, 5), seed=7)
        for e in res.portfolio:
            assert e.report.passed
            assert all(e.report.correctness.values())

    def test_optimized_comparator_beats_known_in_portfolio(self):
        """Optimization preserves the comparator contract and improves its resources."""
        ir = ProblemIR(
            IRKind.ARITHMETIC, known_template="less_than",
            notes={"library_key": "less_than", "resource_bottleneck": True},
        )
        res = run_agent("invention value", ir, sizes=(3, 4, 5), seed=7)
        known = [e for e in res.portfolio if e.provenance == "known"]
        invented = [e for e in res.portfolio if e.provenance == "optimized"]
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


# ===========================================================================
# Triple innovation gate: correct x valuable x novel, none optional
# ===========================================================================

class TestInnovationCertificate:
    """Certificates are relative to the declared set of reference circuits."""

    def _certify(self, gen, **kw):
        cand = generator_to_candidate(gen)
        rep = evaluate_candidate(cand, sizes=(3, 4, 5))
        return cand, rep, certify_innovation(cand, report=rep, **kw)

    def test_positive_control_triple_certified(self):
        _, _, cert = self._certify(_lean_interleaved_increment, baseline_candidates=[_inc_cand()])
        assert cert.correct, cert.reasons
        assert cert.valuable, cert.reasons
        assert cert.novel, cert.reasons
        assert cert.certified
        assert cert.novelty_verdict == "distinct"
        assert "width" in cert.pareto_strict_axes  # saves a qubit at every size
        assert "known:increment-ancilla" in cert.dominated_baselines

    def test_duplicate_of_known_is_not_novel(self):
        _, _, cert = self._certify(build_increment_ancilla_reference)
        assert cert.correct
        assert not cert.novel
        assert not cert.certified
        assert "duplicate_of:known:increment-ancilla" == cert.novelty_verdict

    def test_correct_but_valueless_is_rejected(self):
        # A correct, distinct circuit that is strictly WORSE than the known
        # baseline (one wasted idle ancilla on top of the textbook reference,
        # no compensating win on any axis): not valuable -> not certified.
        def _padded_reference(n: int) -> Circuit:
            base = build_increment_ancilla_reference(n)
            return Circuit(base.n + 1, list(base.gates))

        cand = Candidate(
            "invented:padded", "B", _padded_reference,
            increment_spec, lambda n: n,
            lambda n: list(range(n, _padded_reference(n).n)),
        )
        rep = evaluate_candidate(cand, sizes=(3, 4, 5))
        assert rep.passed  # sandbox-correct...
        cert = certify_innovation(cand, report=rep)
        assert cert.correct  # ...and correct...
        assert not cert.valuable  # ...but Pareto-dominated: no value
        assert not cert.certified

    def test_wrong_function_is_not_correct(self):
        def _decrement_disguised(n: int) -> Circuit:
            from qac.library import build_decrement_wrap_reference
            return build_decrement_wrap_reference(n)

        cand = Candidate(
            "invented:fake", "B", _decrement_disguised,
            increment_spec, lambda n: n,
            lambda n: list(range(n, 2 * n - 1)) if n > 1 else [],
        )
        rep = evaluate_candidate(cand, sizes=(3, 4, 5))
        assert not rep.passed
        cert = certify_innovation(cand, report=rep)
        assert not cert.correct
        assert not cert.certified

    def test_extended_sizes_verified_beyond_campaign(self):
        _, _, cert = self._certify(_lean_interleaved_increment)
        assert set((2, 3, 4, 5, 6)).issubset(set(cert.extended_sizes))

    def test_discovery_result_exposes_certified_subset(self):
        res = run_discovery(problem_key="less_than", mode="opportunity", seed=0, max_batches=5)
        assert res.certified
        for e in res.certified:
            assert e.certificate is not None and e.certificate.certified
            assert e.candidate.id in res.certificates

    def test_portfolio_optimizations_are_all_triple_certified(self):
        ir = ProblemIR(
            IRKind.ARITHMETIC, known_template="less_than",
            notes={"resource_bottleneck": True},
        )
        res = run_agent("triple gate", ir, sizes=(3, 4, 5), seed=7)
        invented = [e for e in res.portfolio if e.provenance == "optimized"]
        assert invented, "certified invention must still be delivered"
        for e in invented:
            assert e.certificate is not None
            assert e.certificate.certified
            assert e.certificate.correct and e.certificate.valuable and e.certificate.novel

    def test_uncertified_never_reaches_portfolio(self):
        ir = ProblemIR(IRKind.ARITHMETIC, notes={"library_key": "increment"})
        res = run_agent(
            "duplicate mutator", ir, sizes=(3, 4, 5),
            track_b_budget=1,
        )
        discovery = run_discovery(
            mutator=MutatorStandIn(pool=[build_increment_ancilla_reference]),
            sizes=(3, 4, 5), mode="necessity", max_batches=1,
        )
        assert discovery.attempts == 1
        assert not discovery.certified, "a duplicate of a known algorithm is not an invention"
        assert any("UNCERTIFIED" in t or "FAIL" in t for t in discovery.trace)
        assert res.delivered
        assert all(entry.provenance == "known" for entry in res.portfolio)

    def test_certificate_json_serializable(self):
        import json

        _, _, cert = self._certify(_lean_interleaved_increment, baseline_candidates=[_inc_cand()])
        blob = json.dumps(cert.to_dict())
        assert '"certified": true' in blob


# ===========================================================================
# Historical top-write incrementer audit
# ===========================================================================

class TestTopwriteAudit:

    def test_exhaustive_semantics_n1_to_8(self):
        for n in range(1, 9):
            rep = check_semantics(_topwrite_increment(n), increment_spec(n), n)
            assert rep.ok, (n, rep.reasons[:3])

    def test_cost_formulas_pinned(self):
        expected = {1: (1, 0, 1, 1), 2: (2, 0, 2, 2), 3: (3, 1, 3, 3)}
        for n in range(1, 9):
            r = _resources(_topwrite_increment(n))
            got = (r["width"], r["toffoli"], r["gates"], r["depth"])
            assert got == expected.get(n, (2 * n - 3, 2 * n - 5, 3 * n - 6, 3 * n - 6)), (n, got)

    def test_strictly_dominates_lean_on_all_axes_n3_to_8(self):
        for n in range(3, 9):
            tw = _resources(_topwrite_increment(n))
            ln = _resources(_lean_interleaved_increment(n))
            for axis in ("width", "toffoli", "gates", "depth"):
                assert tw[axis] < ln[axis], (n, axis, tw, ln)

    def test_matches_shared_prefix_and_beats_original_baselines(self):
        cand = generator_to_candidate(_topwrite_increment)
        rep = evaluate_candidate(cand, sizes=(3, 4, 5))
        assert rep.passed
        cert = certify_innovation(cand, report=rep)
        assert cert.correct and not cert.certified
        assert not cert.valuable and not cert.novel
        assert cert.novelty_verdict == "duplicate_of:reference:shared-prefix-increment"
        assert cert.dominated_baselines == [
            "known:increment-ancilla", "sota:cdkm-increment",
        ]

    def test_historical_replay_does_not_certify_it(self):
        for mode in ("necessity", "opportunity"):
            res = run_discovery(sizes=(3, 4, 5), mode=mode, seed=0, max_batches=2)
            assert res.certificates["invented:topwrite_increment"].correct
            assert not res.succeeded
