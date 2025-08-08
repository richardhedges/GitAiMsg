#!/usr/bin/env python3
import json, os, subprocess, urllib.request, urllib.error, re, pathlib, time, traceback
import hashlib, unicodedata

# ---------- TOML loader ----------
def _read_toml(path: pathlib.Path):
	if not path.exists():
		return {}
	try:
		try:
			import tomllib  # py311+
			with path.open("rb") as f:
				return tomllib.load(f)
		except ModuleNotFoundError:
			import tomli  # pip install tomli for py<=3.10
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
	# Treat this as a BYTE budget now (safer than chars)
	max_diff = int(env("GITAIMSG_MAX_DIFF", aic.get("max_diff_chars", 15000), int))
	temperature = float(env("GITAIMSG_TEMPERATURE", aic.get("temperature", 0.2)))
	top_p = float(env("GITAIMSG_TOP_P", aic.get("top_p", 1.0)))
	system_prompt = env("GITAIMSG_SYSTEM_PROMPT", aic.get("system_prompt", ""))

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
		providers["ollama"]["base_url"] = os.getenv("OLLAMA_URL")

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

# ---------- Git helpers ----------
def sh(cmd: str, default: str = "") -> str:
	# Use git directly; keep output text
	try:
		return subprocess.check_output(
			cmd, shell=True, text=True, stderr=subprocess.DEVNULL
		).strip()
	except subprocess.CalledProcessError:
		return default
	except Exception:
		return default

# Sanitization + validation
ERROR_PREFIXES = (
	"error:", "unexpected token", "syntaxerror", "uncaught",
	"traceback", "referenceerror", "typeerror"
)
CC_SUBJECT = re.compile(
	r'^(feat|fix|chore|docs|refactor|test|build|style|perf)(\([^)]+\))?: .{1,72}$',
	re.I
)

def sanitize_blob(text: str, byte_budget: int) -> str:
	"""Make arbitrary text safe for LLM prompts and clamp size by BYTES."""
	if not text:
		return ""
	# Normalize newlines and unicode; strip NULs
	text = text.replace("\x00", "")
	text = text.replace("\r\n", "\n").replace("\r", "\n")
	text = unicodedata.normalize("NFC", text)
	# Avoid accidental code fences
	text = text.replace("```", "ʼʼʼ").replace("~~~", "∼∼∼")
	# Strict byte clamp
	b = text.encode("utf-8", errors="ignore")
	if len(b) > byte_budget:
		b = b[:byte_budget]
		text = b.decode("utf-8", errors="ignore") + "\n… [truncated]"
	else:
		text = b.decode("utf-8", errors="ignore")
	# Wrap in an opaque block so models don't parse it
	return f"<DIFF>\n<![CDATA[\n{text}\n]]>\n</DIFF>"

def get_branch() -> str:
	for cmd in (
		"git rev-parse --abbrev-ref HEAD",
		"git symbolic-ref --short HEAD",
		"git branch --show-current",
	):
		b = sh(cmd, "")
		if b:
			return b
	return "HEAD"

def get_git_context(max_diff_bytes: int):
	branch = get_branch()
	files = sh("git diff --staged --name-only")
	numstat = sh("git diff --staged --numstat")
	raw_diff = sh("git diff --staged -U0 --no-color")
	digest = hashlib.sha256(raw_diff.encode("utf-8", errors="ignore")).hexdigest()[:12]
	safe_diff = sanitize_blob(raw_diff, max_diff_bytes)
	return branch, files, numstat, safe_diff, digest

# ---------- Logging + fallback ----------
LOG_PATH = pathlib.Path(".git/HOOK_LOG")
def log(msg: str):
	try:
		with LOG_PATH.open("a", encoding="utf-8") as f:
			f.write(f"[gitaimsg {time.strftime('%H:%M:%S')}] {msg}\n")
	except Exception:
		pass

def fallback_message() -> str:
	try:
		ns = sh("git diff --staged --numstat")
		lines = [l for l in ns.splitlines() if "\t" in l]
		files = [l.split("\t", 2)[2] for l in lines]
		adds  = sum(int((l.split("\t", 2)[0] or "0").replace("-", "0")) for l in lines)
		dels  = sum(int((l.split("\t", 2)[1] or "0").replace("-", "0")) for l in lines)
		scopes = sorted({f.split("/",1)[0] for f in files if "/" in f})[:2]
		scope = f"({','.join(scopes)})" if scopes else ""
		return f"chore{scope}: update {len(files)} files (+{adds} -{dels})"
	except Exception:
		return "chore: update files"

def _post_json(url, payload, timeout, headers=None):
	hdrs = {"Content-Type":"application/json", "Accept":"application/json"}
	if headers: hdrs.update(headers)
	body = json.dumps(payload).encode("utf-8")
	req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
	try:
		with urllib.request.urlopen(req, timeout=timeout) as r:
			raw = r.read()
			text = raw.decode("utf-8", errors="replace").strip()
			try:
				return json.loads(text), text
			except json.JSONDecodeError:
				m = re.search(r'\{.*\}\s*$', text, flags=re.S)
				if m:
					try:
						return json.loads(m.group(0)), text
					except json.JSONDecodeError:
						pass
				return None, text
	except urllib.error.HTTPError as e:
		raw = e.read().decode("utf-8", errors="replace")
		return None, raw
	except Exception as e:
		return None, f"<request failed: {e!r}>"

# ---------- Prompt ----------
def build_prompt(branch: str, files: str, numstat: str, safe_diff_block: str, system_prompt: str):
	sys_msg = system_prompt or "You are a senior developer writing concise Conventional Commit messages."
	user_msg = f"""Write ONLY a git commit message.

Constraints:
- First line MUST be: type(scope?): summary (≤ 72 chars). Types: feat|fix|chore|docs|refactor|test|build|style|perf
- Optionally 1–5 bullets on following lines.
- Do NOT include code fences, JSON, or explanations.

Branch:
{branch}

Files staged:
{files}

Changes (numstat):
{numstat}

Diff (opaque block; do not parse syntax inside):
{safe_diff_block}
"""
	return {"system": sys_msg, "user": user_msg}

# ---------- Providers ----------
class Provider:
	def generate(self, prompt): raise NotImplementedError

class Ollama(Provider):
	def __init__(self, base_url, model, timeout_s, temperature, top_p):
		base = base_url.rstrip("/")
		self.url = base + ("/api/generate" if not base.endswith("/api/generate") else "")
		# Slight clamp to reduce “creative” outputs
		self.model, self.timeout_s = model, timeout_s
		self.temperature, self.top_p = min(temperature, 0.2), top_p
	def generate(self, prompt):
		payload = {
			"model": self.model,
			"prompt": f"{prompt['system']}\n\n{prompt['user']}",
			"stream": False,
			"options": {"temperature": self.temperature, "top_p": self.top_p},
		}
		obj, raw = _post_json(self.url, payload, self.timeout_s)
		if obj is None:
			log(f"ollama bad JSON from {self.url}: {raw[:200]}")
			return ""
		return (obj.get("response") or "").strip()

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
				{"role":"system","content":prompt["system"]},
				{"role":"user","content":prompt["user"]},
			],
			"temperature": self.temperature, "top_p": self.top_p, "max_tokens": 300
		}
		obj, raw = _post_json(self.url, payload, self.timeout_s, headers={"Authorization":f"Bearer {self.key}"})
		if obj is None:
			log(f"openai bad JSON from {self.url}: {raw[:200]}")
			return ""
		ch = obj.get("choices",[{}])[0].get("message",{}).get("content","")
		return (ch or "").strip()

class Gemini(Provider):
	def __init__(self, base_url, api_key, model, timeout_s, temperature, top_p):
		self.key = api_key or os.getenv("GEMINI_API_KEY")
		self.base = (base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
		self.model, self.timeout_s, self.temperature, self.top_p = model, timeout_s, temperature, top_p
	def generate(self, prompt):
		if not self.key:
			log("gemini: missing GEMINI_API_KEY")
			return ""
		url = f"{self.base}/models/{self.model}:generateContent"
		payload = {
			"generationConfig": {"temperature": self.temperature, "topP": self.top_p, "maxOutputTokens": 300},
			"contents": [{"role":"user","parts":[{"text": prompt["system"] + "\n\n" + prompt["user"]}]}]
		}
		body = json.dumps(payload).encode("utf-8")
		obj, raw = _post_json(url, payload, self.timeout_s, headers={"X-Goog-Api-Key": self.key})
		if obj is None:
			log(f"gemini bad JSON from {url}: {raw[:200]}")
			return ""
		if "error" in obj:
			log(f"gemini API error: {obj['error']}")
			return ""
		c = obj.get("candidates", [])
		parts = c[0].get("content", {}).get("parts", []) if c else []
		return "".join(p.get("text","") for p in parts).strip()

def build_provider(cfg):
	p = cfg["provider"]
	ps = cfg["providers"].get(p, {})
	model = cfg["model"]; t = cfg["timeout_s"]; temp = cfg["temperature"]; top_p = cfg["top_p"]
	if p == "openai":
		base = ps.get("base_url", "https://api.openai.com/v1")
		key = os.getenv(ps.get("api_key", "OPENAI_API_KEY")) or ps.get("api_key") or ps.get("key")
		return OpenAI(base, key, model, t, temp, top_p)
	if p == "gemini":
		base = ps.get("base_url")
		key = os.getenv(ps.get("api_key", "GEMINI_API_KEY")) or ps.get("api_key", "GEMINI_API_KEY")
		return Gemini(base, key, model, t, temp, top_p)
	base = ps.get("base_url", os.getenv("OLLAMA_URL","http://127.0.0.1:11434"))
	return Ollama(base, model, t, temp, top_p)

# ---------- Validation ----------
def validate_or_fallback(msg: str, fallback: str) -> str:
	if not msg:
		return fallback
	first = msg.splitlines()[0].strip()
	low = first.lower()
	if any(low.startswith(p) for p in ERROR_PREFIXES):
		return fallback
	if not CC_SUBJECT.match(first):
		if ":" in first:  # try to coerce length if it looks like a subject
			t, rest = first.split(":", 1)
			subj = f"{t[:50]}: {rest.strip()[:max(0,72-len(t)-2)]}"
			return subj
		return fallback
	# subject + up to 5 bullets
	lines = [first] + [l for l in msg.splitlines()[1:] if l.strip()]
	return "\n".join(lines[:6]).strip()

# ---------- Main ----------
def main():
	try:
		files = sh("git diff --staged --name-only")
		if not files.strip():
			log("no staged files")
			return

		cfg = load_config()
		branch, files_list, numstat, safe_diff, digest = get_git_context(cfg["max_diff"])

		prompt = build_prompt(branch, files_list, numstat, safe_diff, cfg["system_prompt"])
		provider = build_provider(cfg)

		def gen_once(p):
			try:
				return (provider.generate(p) or "").strip()
			except Exception as e:
				log(f"provider exception: {e!r}")
				return ""

		# Attempt 1: full context (safe diff)
		msg = gen_once(prompt)
		msg = validate_or_fallback(msg, "")

		if not msg:
			# Attempt 2: minimal context (no diff)
			log(f"retry without diff (digest={digest})")
			prompt_no_diff = build_prompt(branch, files_list, numstat, "<DIFF omitted/>", cfg["system_prompt"])
			msg = gen_once(prompt_no_diff)
			msg = validate_or_fallback(msg, fallback_message())

		# Final scrub
		for junk in ("```", "~~~", "{code}", "</code>"):
			msg = msg.replace(junk, "")
		print(msg)
	except Exception as e:
		log("fatal: " + repr(e))
		log(traceback.format_exc())
		return

if __name__ == "__main__":
	main()
