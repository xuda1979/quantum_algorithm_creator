"""Quantum Algorithm Creator (QAC) — minimal reference implementation.

Implements the design in AGENT_DESIGN.md at reduced scale:
  Layer 1  formalization + classical triage          -> qac/triage.py
  Layer 2A Track A retrieval + synthesis             -> qac/track_a.py
  Layer 2B Track B evolutionary discovery            -> qac/track_b.py
  Layer 3  verification & resource sandbox           -> qac/sandbox.py
  Layer 4  ranked solution portfolio                 -> qac/portfolio.py

Pure Python + numpy only, so the whole agent runs anywhere and the
evaluation loop stays deterministic and sub-second (Phase 1 requirement).
"""

__version__ = "0.1.0"
