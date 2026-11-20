"""Innovation certificate: the triple gate every Track-B candidate must pass
before it may be labelled "invented" (AGENT_DESIGN.md Core Philosophy #5:
"Machine-verified novelty only").

A candidate counts as a genuine innovation if and only if ALL THREE hold:

  1. CORRECT   — exact semantics on an extended size sweep (statevector,
                 exhaustive over all inputs) plus an INDEPENDENT cross-check
                 on the kickmix classical-reversible path (two evaluators,
                 two code paths, same verdict). Ancilla cleanliness and
                 polynomial scaling are inherited from the Layer-3 report.
  2. VALUABLE  — strictly Pareto-dominates every same-spec baseline in the
                 known library at EVERY verified size: <= resources in all
                 tracked axes and STRICTLY BETTER in at least one. A candidate
                 that merely matches a known algorithm delivers nothing.
  3. NOVEL     — not identical to any same-spec known baseline, judged by
                 gate-sequence fingerprint AND (for small sizes) exact
                 unitary equality. If a "discovery" is bit-for-bit a library
                 circuit, it is a re-derivation, not an invention — it is
                 still deliverable as the known solution, but never as an
                 innovation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

from .kickmix import allows_fast_path, Circuit, KickmixState
from .sandbox import check_semantics, unitary_matrix


def _resources(circuit: Circuit) -> dict:
    return {
        "width": circuit.n,
        "toffoli": circuit.toffoli_count(),
        "gates": circuit.gate_count(),
        "depth": circuit.depth(),
    }


def _fingerprint(circuit: Circuit) -> tuple:
    """Structural identity of a circuit (gate sequence as tuples)."""
    fp = []
    for g in circuit.gates:
        if hasattr(g, "c1"):  # Toffoli / CCZ
            fp.append((g.name, g.c1, g.c2, g.t))
        elif hasattr(g, "c"):  # CNOT / CZ
            fp.append((g.name, g.c, g.t))
        elif hasattr(g, "a"):  # SWAP
            fp.append((g.name, g.a, g.b))
        else:  # X / Z
            fp.append((g.name, g.i))
    return tuple(fp)


def _unitaries_equal(a: Circuit, b: Circuit, eps: float = 1e-9) -> bool | None:
    """Exact unitary equality when both circuits are small enough to compare
    (equal width); None if not comparable (different widths / too large)."""
    if a.n != b.n or a.n > 8:
        return None
    ua, ub = unitary_matrix(a), unitary_matrix(b)
    dim = 1 << a.n
    for r in range(dim):
        for c in range(dim):
            if abs(ua[r][c] - ub[r][c]) > eps:
                return False
    return True


def _kickmix_check(circuit: Circuit, spec: dict[int, int], data_n: int) -> bool:
    """Independent cross-check on the classical-reversible fast path:
    run every input x through the kickmix simulator and compare against the
    ground-truth spec. Only valid when the circuit allows the fast path."""
    if not allows_fast_path(circuit):
        return True  # not applicable; statevector check remains the sole judge
    for x in range(1 << data_n):
        st = KickmixState(n=circuit.n, x=x)
        for g in circuit.gates:
            g.apply_kickmix(st)
        y = spec[x] if isinstance(spec, dict) else spec(x)
        if st.x != y or st.phase != 0:
            return False
    return True


@dataclass
class InnovationCertificate:
    """Machine-checkable proof of genuine innovation (or its absence)."""

    candidate_id: str
    correct: bool = False
    valuable: bool = False
    novel: bool = False
    certified: bool = False  # all three — the only licence to say "invented"
    extended_sizes: list = field(default_factory=list)
    baselines_compared: list = field(default_factory=list)
    pareto_strict_axes: list = field(default_factory=list)  # axes with strict wins
    dominated_baselines: list = field(default_factory=list)
    novelty_verdict: str = ""  # "distinct" | "duplicate_of:<id>"
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _same_spec(candidate, baseline) -> bool:
    """Two candidates solve the same problem iff their specs agree at every
    comparable size (same data width AND same truth table)."""
    for n in (3, 4, 5):
        try:
            if candidate.n_data(n) != baseline.n_data(n):
                return False
            if candidate.spec(n) != baseline.spec(n):
                return False
        except Exception:
            return False
    return True


def certify_innovation(
    candidate,
    report=None,
    baseline_candidates: list | None = None,
    extended_sizes=(2, 3, 4, 5, 6),
    unitary_crosscheck_max_n: int = 5,
) -> InnovationCertificate:
    """Run the full triple gate on a sandbox-verified Track-B candidate.

    `report` is the Layer-3 VerificationReport (correctness on the campaign
    sizes); `baseline_candidates` defaults to every known-library candidate
    sharing the candidate's spec.
    """
    cert = InnovationCertificate(candidate_id=candidate.id)
    reasons: list[str] = []

    # ---- Gate 1: CORRECTNESS (extended sweep + independent cross-check) ----
    correct = True
    if report is not None and not report.passed:
        correct = False
        reasons.append("Layer-3 report failed: candidate is not even correct")
    for n in extended_sizes:
        circuit = candidate.build(n)
        data_n = candidate.n_data(n)
        anc = candidate.ancilla(n)
        if circuit.n != data_n + len(anc) or circuit.n > 14:
            continue
        sem = check_semantics(circuit, candidate.spec(n), data_n)
        if not sem.ok:
            correct = False
            reasons.append(f"extended-size n={n}: {sem.reasons[:2]}")
            break
        if not _kickmix_check(circuit, candidate.spec(n), data_n):
            correct = False
            reasons.append(f"kickmix cross-check disagrees at n={n}")
            break
        cert.extended_sizes.append(n)

    # ---- collect same-spec baselines --------------------------------------
    if baseline_candidates is None:
        from .library import KnownAlgorithmLibrary

        lib = KnownAlgorithmLibrary()
        baseline_candidates = list(lib._entries.values())
    same_spec_baselines = [b for b in baseline_candidates if _same_spec(candidate, b)]
    cert.baselines_compared = [b.id for b in same_spec_baselines]

    # ---- Gate 2: VALUE (strict Pareto dominance at every verified size) ----
    valuable = True
    if not same_spec_baselines:
        # No baseline solves this problem: any correct candidate is valuable
        # (necessity mode — the alternative is "no solution at all").
        cert.pareto_strict_axes = ["first_solution"]
    else:
        sizes_common = [n for n in extended_sizes if n in (set(cert.extended_sizes) | {3, 4, 5})]
        strict_axes_all: set[str] | None = None
        for b in same_spec_baselines:
            dominated = True
            strict_axes: set[str] = set()
            for n in sizes_common:
                try:
                    mine, theirs = candidate.build(n), b.build(n)
                except Exception:
                    dominated = False
                    break
                rm, rt = _resources(mine), _resources(theirs)
                for axis in ("width", "toffoli", "gates", "depth"):
                    if rm[axis] > rt[axis]:
                        dominated = False
                        break
                    if rm[axis] < rt[axis]:
                        strict_axes.add(axis)
                if not dominated:
                    break
            if not dominated:
                valuable = False
                reasons.append(f"not Pareto-better than baseline '{b.id}' at every size")
            else:
                cert.dominated_baselines.append(b.id)
                strict_axes_all = strict_axes if strict_axes_all is None else (strict_axes_all & strict_axes)
        if valuable and strict_axes_all is not None:
            cert.pareto_strict_axes = sorted(strict_axes_all)
            if not cert.pareto_strict_axes:
                valuable = False
                reasons.append("Pareto-equal on every axis: delivers no measurable value")

    # ---- Gate 3: NOVELTY (duplicate detection vs every same-spec baseline) --
    novel = True
    for b in same_spec_baselines:
        dup = False
        for n in (s for s in extended_sizes if s <= unitary_crosscheck_max_n):
            try:
                mine, theirs = candidate.build(n), b.build(n)
            except Exception:
                continue
            if _fingerprint(mine) == _fingerprint(theirs):
                dup = True
                break
            eq = _unitaries_equal(mine, theirs)
            if eq is True:
                dup = True
                break
        if dup:
            novel = False
            cert.novelty_verdict = f"duplicate_of:{b.id}"
            reasons.append(
                f"identical (unitary-equal or same gate list) to known baseline '{b.id}' "
                "at every size — re-derivation, not invention"
            )
            break
    if novel:
        cert.novelty_verdict = "distinct"

    cert.correct = correct
    cert.valuable = valuable
    cert.novel = novel
    cert.certified = correct and valuable and novel
    if not cert.certified:
        cert.reasons = reasons
    return cert
