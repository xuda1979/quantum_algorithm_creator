"""Run a reproducible circuit audit or discovery campaign."""

from __future__ import annotations

import argparse
import json

from .novelty import InnovationCertificate, certify_innovation
from .portfolio import AgentResult, run_agent
from .problem import IRKind, ProblemIR
from .sandbox import verification_sizes
from .track_b import EVOLUTION_FAMILIES, _lean_interleaved_increment, _topwrite_increment, generator_to_candidate


def demo_increment() -> None:
    print("Incrementer audit and verified fallback")
    ir = ProblemIR(IRKind.ARITHMETIC, notes={"library_key": "increment"})
    _print(run_agent("reversible n-bit increment", ir, seed=7))


def demo_opportunity() -> None:
    print("\nWeighted-threshold circuit discovery")
    ir = ProblemIR(IRKind.ARITHMETIC, known_template="weighted_threshold", notes={"resource_bottleneck": True})
    _print(run_agent("coherent weighted threshold", ir, seed=7))


def demo_reject() -> None:
    print("\nTriage for a one-off database search with QRAM loading")
    ir = ProblemIR(IRKind.ORACLE_SEARCH, known_template="grover", data_loading="qram")
    _print(run_agent("database keyword search", ir, quantum_speedup_poly=2))


def _print(res: AgentResult) -> None:
    for reason in res.triage.reasons + res.notes:
        print(f"  {reason}")
    campaign = res.discovery
    if campaign:
        print(f"  {campaign.attempts} trials | search sizes {campaign.search_sizes} | holdout sizes {campaign.holdout_sizes}")
    for entry in res.portfolio:
        n = entry.report.sizes[-1]
        toffoli, gates, depth, width = entry.score
        print(f"  [{entry.provenance}] {entry.candidate_id} | n={n}, W/T/G/D={width}/{toffoli}/{gates}/{depth}")
        if entry.certificate is not None:
            cert = entry.certificate
            print(f"    Verified at {tuple(cert.extended_sizes)} against {', '.join(cert.baselines_compared)}")


def _search_data(result: AgentResult) -> dict:
    data = {"problem": result.problem, "notes": result.notes, "portfolio": []}
    for entry in result.portfolio:
        report = entry.report.to_dict()
        report.pop("wall_time_s")
        data["portfolio"].append({
            "candidate_id": entry.candidate_id,
            "provenance": entry.provenance,
            "report": report,
            "certificate": entry.certificate.to_dict() if entry.certificate is not None else None,
        })
    discovery = result.discovery
    if discovery is not None:
        data["search"] = {"attempts": discovery.attempts, "search_sizes": discovery.search_sizes,
                          "holdout_sizes": discovery.holdout_sizes, "lineage": discovery.lineage,
                          "trace": discovery.trace}
    return data


def _audit(sizes: tuple[int, ...]) -> list[InnovationCertificate]:
    return [certify_innovation(generator_to_candidate(build), extended_sizes=sizes)
            for build in (_lean_interleaved_increment, _topwrite_increment)]


def _sizes(value: str) -> tuple[int, ...]:
    try:
        return verification_sizes(int(part) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use comma-separated positive integers") from exc


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command")
    audit = subcommands.add_parser("audit", help="recheck the two historical incrementers")
    audit.add_argument("--sizes", type=_sizes, default=tuple(range(1, 9)))
    audit.add_argument("--json", action="store_true")
    search = subcommands.add_parser("search", help="optimize an implemented circuit family")
    search.add_argument("family", choices=(*EVOLUTION_FAMILIES, "increment"))
    search.add_argument("--sizes", type=_sizes, default=(3, 4, 5), help="search sizes, comma separated")
    search.add_argument("--holdout", type=_sizes, help="holdout sizes, comma separated")
    search.add_argument("--seed", type=int, default=0)
    search.add_argument("--batches", type=int, default=16)
    search.add_argument("--no-evolution", action="store_true", help="evaluate only the initial search program")
    search.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "audit":
        certificates = _audit(args.sizes)
        if args.json:
            print(json.dumps([cert.to_dict() for cert in certificates], indent=2))
        else:
            for cert in certificates:
                print(f"{cert.candidate_id} | correct={cert.correct} | {cert.novelty_verdict}")
                for reason in cert.reasons:
                    print(f"  {reason}")
    elif args.command == "search":
        ir = ProblemIR(IRKind.ARITHMETIC, known_template=args.family, notes={"resource_bottleneck": True})
        try:
            result = run_agent(args.family, ir, sizes=args.sizes, holdout_sizes=args.holdout,
                               seed=args.seed, track_b_budget=args.batches, evolve=not args.no_evolution)
        except ValueError as exc:
            parser.error(str(exc))
        if args.json:
            print(json.dumps(_search_data(result), indent=2))
        else:
            _print(result)
    else:
        demo_increment()
        demo_opportunity()
        demo_reject()


if __name__ == "__main__":
    main()
