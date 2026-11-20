# Quantum Algorithm Creator — Agent Design Document

**Version:** 1.2 (2026-11-19)
**Status:** Design specification
**Goal:** An AI agent that takes an unstructured real-world problem and returns a ranked portfolio of machine-verified solutions — a production-grade *existing* quantum algorithm where one fits, and, when necessary or beneficial and feasible, a *novel* algorithm invented by the agent itself. **One problem, many solutions.**

> ## Core Philosophy: Known Algorithms First — Novel Algorithms When the Agent Can, and Often Both
>
> One real-world problem can admit many valid quantum solutions, and this agent is built to produce a **portfolio**, not a single answer:
>
> 1. **A known algorithm is a perfectly good answer.** If the problem maps cleanly onto a known BQP template (Track A) within the qubit/gate/accuracy budget, deliver it — production-grade, verified, resource-estimated. No invention is required to justify the deliverable.
> 2. **Invention triggers in two modes:**
>    - **Necessity mode:** no known algorithm fits the budget, or the required coherent subroutine simply does not exist → Track B attempts to invent one, *if it can*.
>    - **Opportunity mode:** a known solution exists, but budget allows attempting a novel candidate with better resource trade-offs (lower Toffoli count, fewer qubits, shallower depth) → run Track B in parallel and rank the invented candidate alongside the known solution.
> 3. **Portfolio output.** Whenever resources allow, the deliverable is a ranked set of K machine-verified solutions (known and/or invented), letting the user choose by hardware target, cost model, or risk appetite. Multiple distinct solutions to the same problem are a feature, not a redundancy.
> 4. **Honest invention ("if it can").** Track B campaigns are budget- and time-boxed. Failing to invent is an acceptable outcome — the agent then delivers the best known solution plus a precise analysis of the bottleneck it could not crack. What is *never* acceptable is claiming novelty without machine verification.
> 5. **Machine-verified novelty only.** An invented algorithm counts as "new" solely when the sandbox (Layer 3) proves: (a) correctness across sizes with clean ancilla, (b) Pareto-superior or complementary resources versus every baseline in the knowledge graph, and (c) a fitted scaling curve with no hidden exponential blowup.
> 6. **Inventions compound.** Verified discoveries are crystallized as reusable `Bloq`s in Track A's library (Phase 5), permanently expanding the capability frontier so future problems start from a richer baseline.

---

## 0. Executive Summary

Prompting a standalone frontier LLM directly for gate-level quantum code fails on nontrivial tasks because quantum mechanics imposes strict non-classical invariants:

- **Unitarity** — every operation must be reversible;
- **No-cloning theorem** — qubit states cannot be copied;
- **Coherent control flow** — no branching on superpositions;
- **Mandatory uncomputation** — scratch ancilla qubits must return cleanly to $|0\rangle$.

To succeed across both known and uncharted problem classes, the system is a **neuro-symbolic, evaluator-guided multi-agent architecture** that:

1. Separates **mathematical problem formulation** from **circuit realization**;
2. Gates every candidate through a **classical baseline check**;
3. Pairs LLM reasoning with **deterministic quantum verifiers and resource estimators**.

The architecture is a hierarchical **Plan-and-Execute + Maker-Checker** topology with five specialized layers and a dual-track execution router.

---

## 1. End-to-End System Architecture

```text
+-------------------------------------------------------------------------+
|                    USER INPUT: REAL-WORLD PROBLEM                       |
|   (Domain spec, dataset profile, target scale N, precision & SLA)       |
+------------------------------------+------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
| 1. FORMALIZATION & CLASSICAL TRIAGE GATE                                |
|  • Domain-to-Math Agent: Maps business/scientific spec -> canonical IR  |
|    (Ising/HUBO Hamiltonian, Sparse Matrix A, Molecular H, Oracle f(x))  |
|  • Classical Baseline Runner: Tests CP-SAT/Gurobi, Tensor Networks,     |
|    Monte Carlo; audits data-loading (QRAM) & readout bottlenecks        |
+------------------------------------+------------------------------------+
                                     |
                      +--------------+--------------+
                      | Complexity & Novelty Router |
                      +--------------+--------------+
                                     |
             +-----------------------+-----------------------+
             | Maps to known BQP template                    | Bottleneck or novel structure
             v                                               v
+------------------------------------+   +------------------------------------+
| 2A. TRACK A: RETRIEVAL & SYNTHESIS |   | 2B. TRACK B: EVOLUTIONARY DISCOVERY|
|     ("Deliver Good Algorithm")     |   |     ("Create New Algorithm")       |
|  • GraphRAG over Quantum Algorithm |   |  • LLM Mutator / Proposers         |
|    Zoo + verified arXiv primitives |   |    generating parameterized n-qubit|
|  • High-Level Functional Modeling  |   |    DSL motifs or reversible code   |
|    (Classiq Qmod / Qualtran Bloqs) |   |  • MAP-Elites Island Population    |
|  • Hardware- & constraint-aware    |   |  • Fast Small-n & Kickmix          |
|    automatic uncomputation         |   |    Evaluator Loop                  |
+-----------------+------------------+   +------------------+-----------------+
                  |                                         |
                  +--------------------+--------------------+
                                       |
                                       v
+-------------------------------------------------------------------------+
| 3. MULTI-FIDELITY VERIFICATION & RESOURCE ESTIMATION SANDBOX            |
|  • Correctness: Unitary/statevector equivalence (n=2..6), ancilla       |
|    disentanglement check, stabilizer (Stim) & reversible simulators     |
|  • Scaling & Cost: Toffoli/T-count, logical peak qubits, circuit depth, |
|    surface-code physical qubits & runtime under target error budgets    |
+------------------------------------+------------------------------------+
                                     |
                    Pass?            |            Fail / Suboptimal?
              +----------------------+----------------------+
              | Yes                                         | No (Feedback Trace)
              v                                             +---> Loop back to 2A/2B
+------------------------------------+
| 4. RANKED SOLUTION PORTFOLIO       |
|  • K machine-verified solutions:   |
|    known (Track A) & invented      |
|    (Track B), ranked by resources  |
|    per hardware target             |
|  • Per solution: executable        |
|    Qmod/Qualtran/Qiskit, FTQC      |
|    resource spec, quantum-vs-      |
|    classical crossover             |
+------------------------------------+
```

### Core Agent Modules & Responsibilities

#### Layer 1 — Problem Formalizer & Classical Triage Agent
Real-world inputs (e.g., *“minimize battery cathode degradation”* or *"optimize multi-echelon fleet routing"*) are translated into typed mathematical Intermediate Representations (IRs):

- Higher-Order Unconstrained Binary Optimization (HUBO) / Ising Hamiltonians,
- second-quantized fermionic Hamiltonians,
- block-encoded sparse matrices,
- reversible Boolean/phase oracles.

Before invoking quantum synthesis, a **Triage Critic**:

- checks **data-loading complexity** (whether loading classical data requires exponential-cost QRAM versus a compact programmatic circuit);
- estimates whether quantum asymptotic scaling (e.g., quadratic $\mathcal{O}(\sqrt{N})$ vs. superpolynomial speedups) can realistically overcome quantum clock-speed and error-correction overheads against classical solvers.

#### Layer 2A — Algorithmic Retrieval & High-Level Synthesis ("Deliver Good")
When the formal IR matches established paradigms — Quantum Singular Value Transformation (QSVT), Quantum Phase Estimation (QPE), Hamiltonian Simulation, Distributed QAOA, Amplitude Amplification — the agent retrieves verified algorithmic schemas via **Quantum GraphRAG** (indexing mathematical problem structures, oracle assumptions, and complexity bounds).

Crucially, the agent emits **high-level quantum modeling code** in **Classiq Qmod** or **Google Qualtran** `Bloq`s rather than raw gate lists. High-level compilers enforce:

- linear-logic type safety (preventing accidental qubit cloning),
- automatic auxiliary qubit allocation, conjugation, and uncomputation,
- synthesis under hardware qubit/depth constraints.

#### Layer 2B — Evaluator-Guided Discovery Engine ("Create New")
Runs in two modes:

- **Necessity mode:** no known template fits the qubit/gate budget, or the problem demands a novel coherent subroutine, custom ansatz, or new reversible arithmetic architecture;
- **Opportunity mode:** a Track A solution already exists, but budget allows attempting a Pareto-better or complementary novel candidate to run alongside it in the final portfolio.

Instead of one-shot generation, a frontier reasoning model acts inside an **evolutionary search loop** coupled to fast programmatic evaluators (detailed in §2). Campaigns are time-/budget-boxed; failing to invent is an acceptable outcome ("if it can"), and the agent then reports the best known solution plus a precise analysis of the unclosed bottleneck.

#### Layer 3 — Deterministic Verification & Resource Estimation Sandbox
Acts as an incorruptible **Maker-Checker** critic. It:

- validates mathematical exactness (or bounded approximation error),
- verifies clean ancilla reset,
- fits empirical gate scaling across increasing problem sizes,
- propagates logical errors and hardware demands to compute fault-tolerant physical qubit and runtime footprints.

#### Layer 4 — Ranked Solution Portfolio (One Problem, Many Solutions)
The deliverable is a ranked set of K machine-verified solutions rather than a single answer. Each entry contains:

- executable Qmod / Qualtran / Qiskit program,
- asymptotic & FTQC resource specification,
- quantum-vs-classical crossover analysis,
- provenance tag: **known algorithm** (with citation) or **invented by the agent** (with verification evidence: multi-size correctness, ancilla cleanliness, scaling fit, Pareto comparison vs. baselines).

Ranking is per hardware target (NISQ vs. FTQC objective modes, §5), letting the user choose by cost model or risk appetite. Mixed portfolios — e.g., a known QPE-based solution plus an invented low-Toffoli variant — are explicitly supported and encouraged when both verify.

---

## 2. How the Agent Invents New Quantum Algorithms (Track B)

Recent breakthroughs in automated quantum discovery demonstrate that LLMs paired with programmatic evaluators can outperform human-designed state-of-the-art circuits and invent entirely new reversible algorithmic structures (e.g., prime-field reversible `divsteps` for modular inversion, low-Toffoli Reed–Solomon syndrome decoders). Track B requires four specialized design mechanisms:

### 2.1 Size-Parameterized DSL Generation (Small-$n$ → Arbitrary-$N$ Generalization)
Never let the discovery agent synthesize a static circuit for a single fixed qubit count. Force the LLM to output a **Python meta-program or motif-based DSL** (e.g., **Hierarqcal** motifs or parameterized **Qualtran** `Bloq` builders) that takes problem size $n$ as an input argument. Evaluating candidate generators on tiny instances ($n \in \{2,3,4,5\}$) is computationally cheap on classical CPUs/GPUs yet sufficient for discovered algorithms (QFT, Grover variants, novel state-preparation trees) to generalize inductively to arbitrary $n$.

### 2.2 Coherent Redesign & Uncomputation-Aware Search
Converting classical logic into a quantum subroutine often fails because retaining branch history for reversibility consumes massive ancilla memory. Prompt and reward the discovery agent to modify the *underlying algorithmic representation*:

- **branch decoders** — recover past choices from current register states rather than storing history;
- **pebble-game** step-dependent qubit reuse;
- **shared arithmetic units**;
- **bounded failure budgets** — omit circuit logic dedicated to exponentially rare edge-case inputs when the algorithm tolerates tiny bounded error.

### 2.3 Multi-Regime Fast Evaluators
Full statevector simulation scales as $2^n$, stalling evolutionary search above ~30 qubits. The evaluator router dispatches to specialized fast simulators depending on circuit class:

| Evaluator | Circuit class | Method |
| --- | --- | --- |
| **Reversible & Phase-Kickback ("Kickmix") Simulator** | Coherent arithmetic, oracles, data loading built from classical reversible gates ($X$, CNOT, Toffoli, SWAP), diagonal sign gates ($Z$, CZ, CCZ), and valid destructive $X$-basis measurements | Track bitstrings and phases classically on random input samples — validates 60+ qubit subroutines in milliseconds |
| **Stabilizer Simulator (`Stim`)** | Error-correction gadgets, Clifford routing, syndrome extraction | Simulate thousands of qubits at kilohertz shot rates |
| **Small-$n$ Unitary / Tensor-Network Evaluator** | Full quantum interference, block-encodings, non-Clifford variational/non-variational motifs | Check exact fidelity $F = |\mathrm{Tr}(U_{\text{target}}^\dagger U_{\text{cand}})| / 2^n$ on $n \in \{2,\dots,7\}$; verify all $a$ ancilla qubits return cleanly to $|0^a\rangle$ via $V|x\rangle|0^a\rangle = (-1)^{g(x)}|\pi(x)\rangle|0^a\rangle$ |

### 2.4 Island-Model Evolutionary Memory & Agentic RL
Maintain an evolutionary database (MAP-Elites / FunSearch style) partitioned into parallel islands optimizing Pareto trade-offs between **space (peak logical qubits)** and **time (Toffoli / two-qubit gate depth)**. Feed rich diagnostic traces — exact assertion failures, entangled ancilla indices, per-subroutine gate profiles — back into the LLM context window. Over time, distill winning trajectories into mid-sized open weights via tool-augmented reinforcement learning (following paradigms like **Agent-Q** and **QUASAR**) to reduce inference cost during massive search sweeps.

---

## 3. Recommended 2026 Tech Stack

| Architectural Layer | Recommended Frameworks & Tools | Specific Role in the Agent Stack |
| --- | --- | --- |
| **Agent Orchestration** | LangGraph / Ray + Pydantic Tool Schemas | Stateful cyclical graphs for Maker-Checker loops; Ray distributes thousands of parallel circuit evaluations across CPU/GPU workers. |
| **Reasoning & Coding Core** | Frontier Models (Planner/Mutator) + RL-Tuned Open Models (Executor) | Frontier reasoning models handle mathematical reductions and novel algorithmic mutations; smaller fine-tuned models handle boilerplate transpilation and syntax repair. |
| **Knowledge & GraphRAG** | Neo4j + Vector DB indexing *Quantum Algorithm Zoo*, `quant-ph`, QCircuitBench | Structured graph linking problem classes $\to$ reductions $\to$ oracle assumptions $\to$ asymptotic complexities and verified reference code. |
| **High-Level Quantum IR** | **Google Qualtran** & **Classiq Qmod** | Primary output targets for Track A and modular Track B composition; enforces type safety, automatic uncomputation, and bloat-free resource counting. |
| **Gate Compilation & Hybrid ML** | **Qiskit v2.x** (C++ transpiler), **PennyLane**, **NVIDIA CUDA-Q**, **QuCLEAR** | Hardware-native gate synthesis, Clifford extraction/optimization, automatic differentiation, GPU-accelerated hybrid HPC-QC execution. |
| **Verification & Fast Simulation** | Custom Kickmix Simulator, **Google Stim**, TensorNetwork / cuQuantum | Fast correctness verification across reversible logic, Clifford/QEC circuits, and small-$n$ statevectors. |
| **Fault-Tolerant Resource Estimation** | **Qualtran Bloqs** + **Q# / Azure Quantum Resource Estimator** | Computes T-factory overhead, logical error budgets, surface-code distances, and physical qubit-hour estimates to prove practical quantum advantage. |

---

## 4. Phased Development Plan

Build the platform in five sequential phases to avoid over-engineering multi-agent swarms before grounding the evaluation loop.

### Phase 1 — Build the Deterministic Verification & Resource Sandbox First
*Prerequisite before connecting any LLM generation loop.*

Implement a sandboxed Python execution harness wrapping **Qualtran**, **Qiskit v2.x**, **Stim**, and a classical reversible/kickmix simulator. Expose strict JSON/Pydantic-typed evaluation tools that accept a parameterized circuit generator and return:

1. unitarity and ancilla-cleanliness pass/fail flags across $n \in \{2,\dots,6\}$;
2. exact Toffoli, T-gate, two-qubit gate, and peak-qubit counts;
3. fault-tolerant physical resource estimates.

Benchmark this evaluator against **QCircuitBench** tasks so scoring is deterministic and sub-second.

### Phase 2 — Develop Track A Retrieval & High-Level Functional Synthesis
*Targets known quantum speedups via Qmod and Qualtran.*

Construct a structured GraphRAG index over the **Quantum Algorithm Zoo**, standard block-encoding/QSVT templates, and verified Qualtran `Bloq` libraries. Wire a single-agent ReAct loop that retrieves matching algorithmic skeletons and synthesizes high-level **Qmod** or **Qualtran** programs rather than raw OpenQASM. Validate that the agent reliably compiles textbook and industrial formulations (Hamiltonian simulation, QPE, QAOA, linear solvers) with zero ancilla leaks.

### Phase 3 — Add the Problem Formalizer & Classical Triage Front-End
*Prevents false-positive quantum recommendations.*

Build the front-end agent that translates domain specifications (chemistry, combinatorial logistics, quantitative finance, cryptography) into canonical mathematical IRs alongside classical solver baselines (OR-Tools/Gurobi for QUBO/HUBO, classical Monte Carlo for pricing, tensor networks for low-entanglement simulation). Program an explicit **Crossover Calculator** that compares physical quantum runtime estimates (including data loading and shot overhead) against classical scaling curves, rejecting or reformulating problems where classical algorithms win.

### Phase 4 — Launch Track B Evaluator-Guided Evolutionary Discovery
*Requires distributed Ray workers for parallel candidate scoring.*

Wrap a frontier reasoning model inside an asynchronous **MAP-Elites / Island Evolutionary Loop**. When Track A leaves a resource bottleneck (an expensive oracle, state-preparation routine, or coherent arithmetic block), spawn parallel exploration islands that mutate both the mathematical representation (branch decoding, lookup-table pruning, motif composition) and the parameterized circuit generator. Score candidates automatically on multi-size correctness ($n=3,4,5$), asymptotic scaling fit, Toffoli count, and peak qubit width. Run both **necessity-mode** campaigns (no known algorithm fits) and **opportunity-mode** campaigns (attempt Pareto-better variants of verified Track A solutions) so the final deliverable can be a mixed known/invented portfolio.

### Phase 5 — Close the Loop with Library Crystallization & Agentic RL
*Continuous self-improvement from discovered primitives.*

Whenever Track B discovers and formally verifies a Pareto-superior quantum subroutine (lower Toffoli×qubit product than existing baselines), automatically package and register it as a reusable **Qualtran `Bloq`** in Track A's retrieval graph. Concurrently, log all successful and failed mutation trajectories to fine-tune dedicated circuit-synthesis sub-agents via tool-augmented reinforcement learning (similar to **QUASAR** and **Agent-Q**), cutting token costs and accelerating convergence on future real-world problems.

---

## 5. Critical Engineering Guardrails

- **Enforce symbolic scaling fits.** A candidate that passes on $n=3,4$ by brute-force unrolling may hide exponential $\mathcal{O}(2^n)$ gate blowup. The evaluator fits polynomial vs. exponential curves across $n \in \{3,\dots,7\}$ and penalizes complexity growth directly in the evolutionary fitness score.
- **Isolate NISQ vs. Fault-Tolerant objectives.** The hardware target dictates what "good" means. Configure the fitness function dynamically:
  - **NISQ / Early-FTQC mode** — heavily penalize two-qubit gate depth, routing SWAPs, and barren-plateau gradient variance (favor non-variational generators like DQAOA-GPT or shallow adaptive circuits);
  - **Full-FTQC mode** — minimize magic-state factories (**Toffoli / T-count**) and **logical qubit spacetime volume**.

---

## 6. References

- [Evaluator-Guided Quantum Circuit Discovery with LLMs](https://www.youtube.com/watch?v=4sb-0Kcpqk4) — demonstrates how coupling large language models with programmatic evaluators in an evolutionary loop uncovers novel quantum circuit architectures across syndrome decoding, modular arithmetic, and chemistry that outperform expert human designs.
- Quantum Algorithm Zoo — canonical catalogue of quantum algorithm complexity classes.
- Google Qualtran, Classiq Qmod, Google Stim, Qiskit v2.x documentation.
- QCircuitBench — benchmark suite for deterministic circuit evaluation.
- Agent-Q, QUASAR — tool-augmented RL paradigms for agentic distillation.

---

## 7. Implementation Status (Reference Implementation)

A runnable micro-scale reference implementation of this design lives in `qac/` (pure Python + numpy; no external quantum SDKs required):

| Design layer | Module | Status |
| --- | --- | --- |
| Layer 1 — Formalization & Triage | `qac/problem.py` | Implemented: typed IRs, QRAM/quadratic rejection rule, necessity/opportunity router |
| Layer 2A — Retrieval & Synthesis | `qac/library.py` | Implemented: verified parameterized known-algorithm library (micro GraphRAG stand-in) |
| Layer 2B — Evolutionary Discovery | `qac/track_b.py` | Implemented: MAP-Elites archive, budget-boxed campaigns, deterministic mutator stand-in (LLM-swappable) |
| Layer 3 — Verification Sandbox | `qac/sandbox.py`, `qac/kickmix.py` | Implemented: kickmix fast path, exhaustive semantic verification (function + clean ancilla + no stray phase), scaling-fit guardrail, typed reports |
| Layer 4 — Ranked Portfolio | `qac/portfolio.py` | Implemented: provenance-tagged ranked portfolios, mixed known/invented delivery |

**Validated invention (Phase-1/4 milestone):** the discovery engine invented the *lean interleaved incrementer* — by eliminating the textbook incrementer's redundant A₁ ancilla slot and interleaving apply/uncompute top-down (pebble-game scheduling, X(0) strictly last), it saves 1 logical qubit and 2 gates at every size n=1..7 with identical Toffoli count (exhaustively machine-verified; 3 sibling mutation variants were deterministically rejected by the sandbox in the same campaign). Test suite: `python3 -m pytest tests/ -q` → 56 passed. Demo: `python3 -m qac.cli`.

Scaling to the full 2026 stack (Qualtran/Qiskit/Stim backends, Ray-distributed islands, LLM mutators, FTQC resource estimators) follows the five-phase plan in section 4; the `qac/` module boundaries and the sandbox evaluation contract are designed to stay fixed while backends are swapped in.
