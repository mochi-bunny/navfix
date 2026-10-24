"""LLM backend for the Hyperion agents: the Grok Bot client (OpenAI-compatible chat completions).

Same wiring as ~/grokbot-hackathon/GrokBotDemo/src/bot.py: GROK_API_KEY, GROK_MODEL, GROK_BASE_URL
from that project's .env. With a key the agents call xAI Grok. Without one they call a local Ollama
model through the SAME interface, and every logged decision carries the backend name so a run is
never presented as Grok when it was not.
"""
import json, os, re, time, urllib.request, urllib.error

ENV_PATH = os.path.expanduser("~/grokbot-hackathon/GrokBotDemo/.env")


def _load_env(path=ENV_PATH):
    out = {}
    try:
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


class Brain:
    """chat(system, user) -> (text, meta). meta = {backend, model, latency_s, ok, error}."""

    def __init__(self, force_local=False):
        self.calls, self.failures, self.total_latency = 0, 0, 0.0
        if os.environ.get("HYPERION_LLM", "claude") == "claude" and not force_local:
            # hyperion-fo: Claude through the Claude Code CLI (headless, no tools, no MCP servers)
            self.backend, self.model, self.base, self.key = "claude-cli", os.environ.get("CLAUDE_MODEL", "sonnet"), "", ""
            return
        env = {**_load_env(), **{k: v for k, v in os.environ.items() if k.startswith("GROK_")}}
        key = env.get("GROK_API_KEY", "").strip()
        if key and not force_local:
            self.backend, self.base = "xai-grok", env.get("GROK_BASE_URL", "https://api.x.ai/v1")
            self.model, self.key = env.get("GROK_MODEL", "grok-4.3"), key
        else:
            self.backend, self.base = "local-fallback", os.environ.get("LOCAL_LLM_URL", "http://localhost:11434/v1")
            self.model, self.key = os.environ.get("LOCAL_LLM_MODEL", "qwen2.5:3b"), "ollama"
        self.calls, self.failures, self.total_latency = 0, 0, 0.0

    def chat(self, system, user, max_tokens=400, timeout=60):
        if self.backend == "claude-cli":
            return self._chat_claude(system, user, timeout)
        body = {"model": self.model, "temperature": 0.2, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        req = urllib.request.Request(self.base.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.key})
        t0, meta = time.time(), {"backend": self.backend, "model": self.model}
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
            text = data["choices"][0]["message"]["content"] or ""
            meta.update(ok=True, error="")
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, OSError) as e:
            text, meta = "", {**meta, "ok": False, "error": str(e)[:160]}
            self.failures += 1
        meta["latency_s"] = round(time.time() - t0, 2)
        self.calls += 1
        self.total_latency += meta["latency_s"]
        return text, meta

    def _chat_claude(self, system, user, timeout):
        import subprocess, tempfile
        t0, meta = time.time(), {"backend": self.backend, "model": self.model}
        cmd = ["claude", "-p", user, "--model", self.model, "--output-format", "json", "--system-prompt", system,
               "--strict-mcp-config", "--disallowedTools=Bash,Edit,Write,Read,WebFetch,WebSearch,Glob,Grep,Task"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=tempfile.gettempdir())
            d = json.loads(r.stdout)
            text = d.get("result", "") or ""
            meta.update(ok=not d.get("is_error", False), error="" if not d.get("is_error") else text[:160],
                        cost_usd=d.get("total_cost_usd"))
        except (subprocess.TimeoutExpired, ValueError, OSError) as e:
            text, meta = "", {**meta, "ok": False, "error": str(e)[:160]}
            self.failures += 1
        meta["latency_s"] = round(time.time() - t0, 2)
        self.calls += 1
        self.total_latency += meta["latency_s"]
        return text, meta

    def chat_json(self, system, user, **kw):
        """Ask for a JSON object; returns (obj_or_None, meta). Parsing failures are reported, not hidden."""
        text, meta = self.chat(system + "\nReply with ONE JSON object and nothing else.", user, **kw)
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0)), meta
            except ValueError:
                pass
        meta["ok"] = False
        meta["error"] = meta.get("error") or "unparseable JSON"
        meta["raw"] = text[:200]
        return None, meta
