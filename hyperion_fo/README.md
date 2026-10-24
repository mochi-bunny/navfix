# Hyperion × Fo

When one of 20 warehouse robots (NVIDIA Isaac Sim) breaks, a code-enforced dispatcher has **Fo** (Wajo) call the on-call
technician, book the visit on the calendar, and email the incident report. The technician's ETA feeds back into the sim.

- `fleet.py`, `warehouse_scene.py`, `warehouse_live.py`, `sim_bridge.py`: the Isaac Sim fleet and its telemetry bridge.
- `agents.py`, `dispatch.py`, `llm.py`: the agents and the Claude dispatcher.
- `policy.py`, `policy_hook.py`, `test_policy.py`: the trust layer (allowlist, one call per incident, human yes for
  spend), enforced in code.
- `ops_live.py`, `panel.html`, `pusher.py`: the live ops panel.
- `contacts.example.json`: copy it to `contacts.json` (gitignored) with your own consenting contacts.
- `PLAN.md`, `SUBMISSION.md`: design and write-up.

Built by Megha, Ferbin & Thiago (Wajo × Anthropic hackathon, Oct 2026).
