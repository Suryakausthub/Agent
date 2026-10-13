"""Groq client wrapper.

Groq exposes an OpenAI-compatible endpoint, so this uses the `openai` SDK
pointed at Groq's base URL. Swapping providers later is a base_url + model
change, not a rewrite.

Two guardrails are enforced here rather than left to the caller, because the
model cannot be trusted to respect them on its own (spec section 15):

  * Numbers are masked before the request and restored after. The model never
    sees a digit it could copy incorrectly, and never emits one we then have to
    trust. Any digit appearing in output that did not come from an unmasked
    placeholder is a hard failure (spec 15.3).
  * temperature is pinned to 0 for every factual call (spec 15.7).
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

from openai import OpenAI
from openai import APIConnectionError, APIStatusError, RateLimitError


class LLMError(RuntimeError):
    pass


class HallucinatedNumber(LLMError):
    """Model emitted a digit that was not one of the masked placeholders."""


@dataclass
class LLMResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    masked: dict[str, str] = field(default_factory=dict)


_NUM_RE = re.compile(r"\d[\d,._%+/x×-]*\d|\d")


def mask_numbers(text: str) -> tuple[str, dict[str, str]]:
    """Replace every numeric run with an opaque placeholder.

    "reduced latency by 40%" -> "reduced latency by <N0>"

    The model is told to carry placeholders through untouched. Because they
    contain no digits, the model cannot silently alter a metric, and any digit
    in the output is provably invented.
    """
    mapping: dict[str, str] = {}

    def repl(m: re.Match[str]) -> str:
        key = "<N%d>" % len(mapping)
        mapping[key] = m.group(0)
        return key

    return _NUM_RE.sub(repl, text), mapping


def unmask_numbers(text: str, mapping: dict[str, str]) -> str:
    for key, val in mapping.items():
        text = text.replace(key, val)
    return text


_PLACEHOLDER_RE = re.compile(r"<N\d+>")


def assert_no_invented_numbers(text: str, mapping: dict[str, str]) -> None:
    """Called on model output BEFORE unmasking.

    Placeholders themselves contain digits, so they must be removed before
    scanning for invented numbers -- otherwise every valid placeholder is a
    false positive.
    """
    for key in _PLACEHOLDER_RE.findall(text):
        if key not in mapping:
            raise HallucinatedNumber("model invented placeholder %s" % key)

    stripped = _PLACEHOLDER_RE.sub("", text)
    leftover = _NUM_RE.findall(stripped)
    if leftover:
        raise HallucinatedNumber(
            "model emitted digits not present in source: %r" % leftover[:5]
        )


_RETRY_IN = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s", re.I)


def _rate_limit_wait(err: Exception, attempt: int) -> float:
    """How long to wait before retrying a 429.

    Groq's per-minute token limit says exactly when capacity returns ("try
    again in 7.4s"). Waiting that long lets a burst of calls -- tailoring is
    one call per bullet -- ride the limit out instead of exhausting three
    1-2-4 second retries and dropping to the fallback model, which extracts
    differently and so moves the score. A per-DAY limit is not worth waiting
    for: retry quickly, and let the fallback or the caller handle it.
    """
    msg = str(err)
    m = _RETRY_IN.search(msg)
    if m and "per day" not in msg.lower():
        wait = int(m.group(1) or 0) * 60 + float(m.group(2)) + 1.0
        if wait <= 75:
            return wait
    return min(2 ** attempt, 8)


def _load_dotenv(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


class LLM:
    def __init__(self, cfg: dict, env_path: str | None = None):
        if env_path:
            _load_dotenv(env_path)
        key = os.environ.get("GROQ_API_KEY")
        if not key:
            raise LLMError("GROQ_API_KEY is not set (looked in env and .env)")
        self.cfg = cfg
        self.model = cfg["model"]
        self.fallback = cfg.get("fallback_model")
        self.client = OpenAI(
            api_key=key,
            base_url=cfg["base_url"],
            timeout=cfg.get("request_timeout_s", 90),
            max_retries=0,          # retried explicitly below so we can fall back
        )
        self.warnings: list[str] = []
        if cfg.get("verify_models", True):
            self._verify_models()

    def _verify_models(self) -> None:
        """Confirm the configured models are reachable on this key.

        A fallback that 404s is worse than no fallback: it only fails once the
        primary is already failing, turning a recoverable rate-limit into a
        hard error. Checking at startup surfaces it immediately.
        """
        try:
            available = {m.id for m in self.client.models.list().data}
        except Exception as e:                     # network/auth issues
            self.warnings.append("could not list models: %s" % e)
            return
        if self.model not in available:
            raise LLMError(
                "model %r is not available on this API key; available: %s"
                % (self.model, ", ".join(sorted(available)[:8])))
        if self.fallback and self.fallback not in available:
            self.warnings.append(
                "fallback model %r is not available on this API key - "
                "disabling fallback" % self.fallback)
            self.fallback = None

    def complete(
        self,
        system: str,
        user: str,
        *,
        json_schema: dict | None = None,
        effort: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        kwargs: dict = {
            "model": model or self.model,
            "temperature": self.cfg.get("temperature", 0.0),
            "max_tokens": max_tokens or self.cfg.get("max_tokens", 4096),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "strict": True,
                                "schema": json_schema},
            }
        eff = effort or self.cfg.get("reasoning_effort")
        if eff:
            kwargs["reasoning_effort"] = eff

        attempts = self.cfg.get("max_retries", 3)
        last: Exception | None = None
        # Per-minute limits are waited out (up to this budget) WITHOUT using
        # up an attempt: the minute window always reopens, and giving up after
        # three short waits sent a burst of calls to the weaker fallback model
        # -- or failed project selection outright.
        minute_budget = self.cfg.get("rate_limit_wait_s", 120)
        waited = 0.0
        i = 0
        while i < attempts:
            try:
                resp = self.client.chat.completions.create(**kwargs)
                usage = resp.usage
                return LLMResult(
                    text=resp.choices[0].message.content or "",
                    model=kwargs["model"],
                    prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                )
            except RateLimitError as e:
                last = e
                wait = _rate_limit_wait(e, i)
                per_minute = "per day" not in str(e).lower()
                if per_minute and waited + wait <= minute_budget:
                    waited += max(wait, 2.0)
                    time.sleep(max(wait, 2.0))
                    continue                     # not counted as an attempt
                time.sleep(wait)
                i += 1
                continue
            except APIConnectionError as e:
                last = e
                time.sleep(min(2 ** i, 8))
                i += 1
            except APIStatusError as e:
                # 4xx other than 429 will not succeed on retry
                if e.status_code and 400 <= e.status_code < 500:
                    raise LLMError("Groq rejected the request: %s" % e) from e
                last = e
                time.sleep(min(2 ** i, 8))
                i += 1

        if self.fallback and kwargs["model"] != self.fallback:
            kwargs["model"] = self.fallback
            kwargs.pop("reasoning_effort", None)   # not supported on every model
            try:
                resp = self.client.chat.completions.create(**kwargs)
            except (RateLimitError, APIConnectionError, APIStatusError) as e:
                # The fallback was unguarded, so a rate-limited fallback raised
                # a raw SDK exception straight past every caller's `except
                # LLMError`. Callers must only ever see this module's own
                # exception types.
                raise LLMError(
                    "primary failed (%s) and fallback %s also failed: %s"
                    % (last, self.fallback, e)) from e
            usage = resp.usage
            return LLMResult(
                text=resp.choices[0].message.content or "",
                model=self.fallback,
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            )
        raise LLMError("all attempts failed: %s" % last)

    def rewrite_masked(self, system: str, source_text: str, **kw) -> LLMResult:
        """Number-safe rewrite: mask, call, verify, unmask."""
        masked, mapping = mask_numbers(source_text)
        res = self.complete(system, masked, **kw)
        assert_no_invented_numbers(res.text, mapping)
        res.text = unmask_numbers(res.text, mapping)
        res.masked = mapping
        return res
