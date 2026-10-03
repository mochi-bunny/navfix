"""Commissioning gate: Thompson-sampling bandit with the confidence rule verified in the
integration notes. Standard library only.

A subject (robot skill or route) has candidate policies ("arms"). Each trial succeeds or fails.
The gate opens only when the best policy has P(success rate >= 0.8) >= 0.95.
"""
import random
import re
import subprocess
import time
from math import comb

THR, CONF = 0.8, 0.95


def p_at_least(s, f, thr=THR):
    """P(true rate >= thr) under a Beta(1+s, 1+f) posterior, which equals P(Binomial(s+f+1, thr) <= s)."""
    n = s + f + 1
    return sum(comb(n, k) * thr**k * (1 - thr) ** (n - k) for k in range(s + 1))


def commission(arms, run_batch, thr=THR, conf=CONF, batch=4, max_trials=40, on_batch=None, rng=random):
    """arms: candidate policy ids. run_batch({arm: n_episodes}) -> {arm: [1, 0, ...]}, run in parallel.
    on_batch(entry) is called after every batch so a dashboard can show progress."""
    s, f = dict.fromkeys(arms, 0), dict.fromkeys(arms, 0)
    log = []
    while sum(s.values()) + sum(f.values()) < max_trials:
        picks = [max(arms, key=lambda a: rng.betavariate(1 + s[a], 1 + f[a])) for _ in range(batch)]
        t0 = time.time()
        results = run_batch({a: picks.count(a) for a in arms if a in picks})
        for arm, outcomes in results.items():
            s[arm] += sum(outcomes)
            f[arm] += len(outcomes) - sum(outcomes)
        p = {a: p_at_least(s[a], f[a], thr) for a in arms}
        best = max(arms, key=p.get)
        entry = {
            "batch": len(log) + 1,
            "ran": results,
            "counts": {a: f"{s[a]}/{s[a] + f[a]}" for a in arms},
            "p_at_least": {a: round(p[a], 3) for a in arms},
            "best": best,
            "seconds": round(time.time() - t0, 1),
        }
        entry["line"] = (f"batch {entry['batch']}: " + ", ".join(f"{a} {entry['counts'][a]}" for a in arms)
                         + f" | P(rate >= {thr:.0%}) for {best} = {p[best]:.2f}")
        log.append(entry)
        if on_batch:
            on_batch(entry)
        if p[best] >= conf:
            return {"decision": "pass", "policy": best, "successes": s, "failures": f, "log": log}
        if all(v <= 1 - conf for v in p.values()):
            return {"decision": "fail", "successes": s, "failures": f, "log": log}
    return {"decision": "inconclusive", "successes": s, "failures": f, "log": log}


def sim_runner(rates, seconds_per_batch=1.5, rng=random):
    """Trials are coin flips at fixed true rates. No GPU; for routes and for testing."""
    def run_batch(alloc):
        time.sleep(seconds_per_batch)
        return {a: [1 if rng.random() < rates[a] else 0 for _ in range(n)] for a, n in alloc.items()}
    return run_batch


ROLLOUT = ("gr00t/eval/sim/LIBERO/libero_uv/.venv/bin/python gr00t/eval/rollout_policy.py "
           "--n-episodes {n} --n-envs {envs} --policy-client-host 127.0.0.1 --policy-client-port {port} "
           "--max-episode-steps {steps} --n-action-steps 8 --env-name {env}")
RESULTS = re.compile(r"results:\s+\('[^']*',\s*\[([^\]]*)\]")
VIDEOS = re.compile(r"Video saved to:\s+(\S+)")


def gr00t_runner(env, ports, container="cell", steps=720, on_clips=None, timeout=900):
    """Trials are real LIBERO episodes driven by GR00T policy servers. Each arm's share of a batch
    runs as one rollout_policy.py process in the cell container, and all arms run in parallel.

    Each process uses a single env. rollout_policy.py stops at the first n finished episodes and its
    envs auto-reset, so with parallel envs quick successes can finish twice while a slow failure is
    cut off and dropped. That would inflate success rates and could open the gate falsely."""
    def run_batch(alloc):
        procs = {}
        for arm, n in alloc.items():
            cmd = ROLLOUT.format(n=n, envs=1, port=ports[arm], steps=steps, env=env)
            procs[arm] = subprocess.Popen(["docker", "exec", container, "bash", "-c", cmd],
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        out, errors = {}, []
        for arm, p in procs.items():
            try:
                text, _ = p.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                p.kill()
                errors.append(f"{arm}: rollout timed out after {timeout}s")
                continue
            m = RESULTS.search(text)
            if p.returncode != 0 or not m:
                errors.append(f"{arm}: rollout failed (exit {p.returncode}): {text.strip()[-400:]}")
                continue
            out[arm] = [1 if x.strip() == "True" else 0 for x in m.group(1).split(",") if x.strip()][:alloc[arm]]
            v = VIDEOS.search(text)
            if v and on_clips:
                on_clips(arm, v.group(1))
        if errors:
            raise RuntimeError("; ".join(errors))
        return out
    return run_batch
