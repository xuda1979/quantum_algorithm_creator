"""Demo entry point: run the agent on a sample problem.

Usage:  python -m qac.cli
"""

from __future__ import annotations

import json

from .problem import IRKind, ProblemIR
from .portfolio import run_agent
from .library import KnownAlgorithmLibrary


def demo_increment() -> None:
    print("=" * 72)
    print("DEMO 1 — necessity mode: reversible incrementer, no known template")
    print("=" * 72)
    ir = ProblemIR(
        kind=IRKind.ARITHMETIC,
        known_template=None,
        notes={"library_key": "increment-missing"},
    )
    res = run_agent("reversible n-bit increment", ir, sizes=(3, 4, 5), seed=7)
    _print(res)


def demo_opportunity() -> None:
    print()
    print("=" * 72)
    print("DEMO 2 — opportunity mode: known incrementer + bottleneck flag")
    print("=" * 72)
    ir = ProblemIR(
        kind=IRKind.ARITHMETIC,
        known_template="increment",
        notes={"library_key": "increment", "resource_bottleneck": True},
    )
    res = run_agent("reversible n-bit increment (bottleneck flagged)", ir, sizes=(3, 4, 5), seed=7)
    _print(res)


def demo_reject() -> None:
    print()
    print("=" * 72)
    print("DEMO 3 — triage rejection: QRAM loading + quadratic speedup only")
    print("=" * 72)
    ir = ProblemIR(
        kind=IRKind.ORACLE_SEARCH,
        known_template="grover",
        data_loading="qram",
        notes={},
    )
    res = run_agent("database keyword search over N=2^n rows", ir)
    _print(res)


def _print(res) -> None:
    print(f"triage: track_a={res.triage.track_a} track_b_mode={res.triage.track_b_mode}")
    for r in res.triage.reasons:
        print(f"  - {r}")
    for n in res.notes:
        print(f"  note: {n}")
    if not res.portfolio:
        print("  portfolio: (empty)")
        return
    print(f"  portfolio ({len(res.portfolio)} solution(s), ranked):")
    for i, e in enumerate(res.portfolio, 1):
        m = e.report.sizes[-1]
        line = (
            f"   {i}. [{e.provenance:8s}] {e.candidate_id:32s} "
            f"toffoli@{m}={e.report.toffoli.get(m)} gates@{m}={e.report.gate_count.get(m)} "
            f"depth@{m}={e.report.depth.get(m)} fidelity@{m}={e.report.correctness.get(m)}"
        )
        if e.provenance == "invented" and e.certificate is not None:
            c = e.certificate
            line += (
                f" | certificate: correct={c.correct} valuable={c.valuable} novel={c.novel}"
                f" (strict wins: {','.join(c.pareto_strict_axes) or '-'};"
                f" novelty: {c.novelty_verdict})"
            )
        print(line)


if __name__ == "__main__":
    demo_increment()
    demo_opportunity()
    demo_reject()
