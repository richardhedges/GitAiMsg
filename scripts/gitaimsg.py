#!/usr/bin/env python3
import json, os, sys, subprocess, urllib.request, urllib.error, pathlib

# ---------- TOML loader ----------
def _read_toml(path: pathlib.Path):
	if not path.exists():
		return {}
	try:
		try:
			import tomllib # py311+
			with path.open("rb") as f:
				return tomllib.load(f)
		except ModuleNotFoundError:
			import tomli # pip install tomli for py<=3.10
			with path.open("rb") as f:
				return tomli.load(f)
	except Exception:
		return {}

# ---------- Config ----------
def load_config():
	repo_cfg = _read_toml(pathlib.Path.cwd() / ".gitaimsg.toml")
	user_cfg = _read_toml(pathlib.Path.home() / ".config" / "gitaimsg" / "config.toml")
	cfg = {**user_cfg, **repo_cfg}
	aic = cfg.get("gitaimsg", {})
	def env(name, default=None, cast=None):
		v = os.getenv(name)
		if v is None:
			return default
		return cast(v) if cast else v

	provider = env("GITAIMSG_PROVIDER", aic.get("provider", "ollama")).lower()
	model = env("GITAIMSG_MODEL", aic.get("model", "qwen2.5-coder:7b"))
	timeout_s = int(env("GITAIMSG_TIMEOUT_S", aic.get("timeout_s", 30), int))
	max_diff = int(env("GITAIMSG_MAX_DIFF", aic.get("max_diff_chars", 15000), int))
	temperature = float(env("GITAIMSG_TEMPERATURE", aic.get("temperature", 0.2)))
	top_p = float(env("GITAIMSG_TOP_P", aic.get("top_p", 1.0)))
	system_prompt = env("GITAIMSG_SYSTEM_PROMPT", aic.get("system_prompt", ""))

	# provider blocks
	providers = {
		"ollama": {
			**cfg.get("provider.ollama", {}),
			**cfg.get("provider", {}).get("ollama", {})
		},
		"openai": {
			**cfg.get("provider.openai", {}),
			**cfg.get("provider", {}).get("openai", {})
		},
		"gemini": {
			**cfg.get("provider.gemini", {}),
			**cfg.get("provider", {}).get("gemini", {})
		},
	}

	if os.getenv("OLLAMA_URL"):
		provider["ollama"]["base_url"] = os.getenv("OLLAMA_URL")

	return {
		"provider": provider,
		"model": model,
		"timeout_s": timeout_s,
		"max_diff": max_diff,
		"temperature": temperature,
		"top_p": top_p,
		"system_prompt": system_prompt,
		"providers": providers,
	}

# ---------- Git context ----------
def sh(cmd: str) -> str:
	return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL).strip()

def get_git_context(max_diff: int):
	branch = sh("git rev-parse --abrev-ref HEAD")
	files = sh("git diff --staged --name-only")
	diff = sh("git diff --staged -U0 --no-color")
	if len(diff) > max_diff:
		diff = diff[:max_diff] + "\n... [diff turuncated]"
	return branch, files, diff

def build_prompt(branch: str, files: str, diff: str, system_prompt: str):
	sys_msg = system_prompt or "You are a senior developer writing concise Conventional Commit messages."
	user_msg = f"""Write a clear git commit message.

Rules:
- Prefer Conventional Commits: feat/fix/chore/docs/refactor/test/build/style/perf
- Subject <= 72 chars, imperative mood.
- If helpful, add 1-5 bullet points body.
- No code fences. No markdown headers.

Context:
Branch: {branch}

Files staged:
{files}

Diff (truncated if long):
{diff}

Output ONLY the message (subject + optional body)."""

# ---------- Providers ----------
class Provider:
	def generate(self, prompt): raise NotImplementedError

class Ollama(Provider):
	def __init__(self, base_url, model, timeout_s, temperature, top_p):
		self.url = (base_url.rstrip("/") + "/api/generate") if "/api" not in base_url else base_url
		self.model, self.timeout_s, self.temperature, self.top_p = model, timeout_s, temperature, top_p
	def generate(self, prompt):
		data = {
			"model": self.model,
			"prompt": f"{prompt['system']}\n\n{prompt['user']}",
			"stream": false,
			"options": {"temperature": self.temperature, "top_p": self.top_p},
		}
		req = urllib.request.Request(self.url, data=json.dumps(data).encode(), headers={"Content-Type":"application/json"})
		with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
			return json.loads(r.read().decode()).get("response","").strip()

class OpenAI(Provider):
	def __init__(self, base_url, api_key, model, timeout_s, temperature, top_p):
		self.url = base_url.rstrip("/") + "/chat/completions"
		self.key, self.model, self.timeout_s = api_key or os.getenv("OPENAI_API_KEY"), model, timeout_s
		self.temperature, self.top_p = temperature, top_p
	def generate(self, prompt):
		if not self.key: return ""
		payload = {
			"model": self.model,
			"messages": [
				{"role":"system","context":prompt["system"]},
				{"role":"user","context":prompt["user"]},
			],
			"temperature": self.temperature, "top_p": self.top_p, "max_tokens": 300
		}
		req = urllib.request.Request(self.url, data=json.dumps(payload).encode(),headers={"Content-Type":"application/json","Authorization":f"Bearer {self.key}"})
		with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
			obj = json.loads(r.read().decode())
			return obj.get("choices",[{}])[0].get("message",{}).get("context","").strip()

class Gemini(Provider):
	def __init__(self, base_url, api_key, model, timeout_s, temperature, top_p):
		self.key = api_key or os.getenv("GEMINI_API_KEY")
		self.base = (base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
		self.model, self.timeout_s, self.temperature, self.top_p = model, timeout_s, temperature, top_p
	def generate(self, prompt):
		if not self.key: return ""
		url = f"{self.base}/models/{self.model}:generateContent?key={self.key}"
		payload = {
			"generationConfig": {"temperature": self.temperature, "topP": self.top_p, "maxOutputTokens": 300},
			"contents": [{"role":"user","parts":[{"text": prompt["system"] + "\n\n" + prompt["user"]}]}]
		}
		req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type":"application/json"})
		with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
			obj = json.loads(r.read().decode())
			c = obj.get("candidates", [])
			if not c: return ""
			parts = c[0].get("content", {}).get("parts", [])
			return "".join(p.get("text","") for p in parts).strip()

def build_provider(cfg):
	p = cfg["provider"]
	ps = cfg["providers"].get(p, {})
	model = cfg["model"]; t = cfg["timeout_s"]; temp = cfg["temperature"]; top_p = cfg["top_p"]
	if p == "openai":
		base = ps.get("base_url", "https://api.openai.com/v1")
		key = os.getenv(ps.get("api_key_env","OPENAI_API_KEY"))
		return OpenAI(base, key, model, t, temp, top_p)
	if p == "gemini":
		base = ps.get("base_url")
		key = os.getenv(ps.get("api_key_env","GEMINI_API_KEY"))
		return Gemini(base, key, model, t, temp, top_p)
	base = ps.get("base_url", os.getenv("OLLAMA_URL","http://127.0.0.1:11434"))
	return Ollama(base, model, t, temp, top_p)

# ---------- Main ----------
def main():
	try:
		files = sh("git diff --staged --name-only")
		if not files.strip():
			return
		cfg = load_config()
		branch, _, diff = get_git_context(cfg["max_diff"])
		prompt = build_prompt(branch, files, diff, cfg["system_prompt"])
		provider = build_provider(cfg)
		msg = provider.generate(prompt).strip()
		for trash in ("```", "assistant:", "model:", "output:"):
			msg = msg.replace(trash, "")
		print(msg.strip())
	except Exception:
		return

if __name__ == "__main__":
	main()