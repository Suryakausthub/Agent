"""Verify number masking offline, then confirm the Groq key works live."""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yaml
from app.core.llm import (LLM, HallucinatedNumber, assert_no_invented_numbers,
                          mask_numbers, unmask_numbers)

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")

print("=" * 62)
print("1. NUMBER MASKING (offline, no API)")
print("=" * 62)
samples = [
    "Debugged and resolved 100+ issues involving REST APIs.",
    "Built a distributed platform for 10,000+ log events.",
    "Processed 1M+ structured records using distributed processing.",
    "CodeChef 4 star (1901) and Codeforces Expert (1775) - top 2-5%.",
]
for s in samples:
    masked, m = mask_numbers(s)
    back = unmask_numbers(masked, m)
    ok = "OK " if back == s else "FAIL"
    print("  %s %s" % (ok, masked))
    print("       roundtrip -> %s" % ("identical" if back == s else "MISMATCH: " + back))

print()
print("  guard: model returns an invented digit ->", end=" ")
try:
    assert_no_invented_numbers("improved throughput by 45%", {})
    print("FAIL (not caught)")
except HallucinatedNumber as e:
    print("caught correctly")

print("  guard: model returns an unknown placeholder ->", end=" ")
try:
    assert_no_invented_numbers("improved by <N7>", {"<N0>": "40"})
    print("FAIL (not caught)")
except HallucinatedNumber:
    print("caught correctly")

print()
print("=" * 62)
print("2. LIVE GROQ CALL")
print("=" * 62)
cfg = yaml.safe_load(open(os.path.join(ROOT, "backend", "data", "config.yaml"),
                          encoding="utf-8"))
llm = LLM(cfg["llm"], env_path=os.path.join(ROOT, ".env"))
print("  base_url: %s" % cfg["llm"]["base_url"])
print("  model:    %s" % cfg["llm"]["model"])

res = llm.complete(
    "You are a terse assistant. Reply with exactly one word.",
    "Reply with the single word: connected",
)
print("  response: %r" % res.text.strip())
print("  model:    %s" % res.model)
print("  tokens:   %d in / %d out" % (res.prompt_tokens, res.completion_tokens))

print()
print("=" * 62)
print("3. LIVE MASKED REWRITE (the real guardrail path)")
print("=" * 62)
src = "Debugged and resolved 100+ issues involving REST APIs, SQL queries, backend services, and application logs."
system = (
    "Rewrite the bullet to be more concise. Use ONLY facts, technologies and "
    "words present in the input. Do not add any technology or tool not already "
    "there. Placeholders like <N0> are opaque tokens: reproduce them exactly, "
    "never replace them with a number. Reply with the rewritten bullet only."
)
print("  source:  %s" % src)
masked, mapping = mask_numbers(src)
print("  masked:  %s" % masked)
try:
    out = llm.rewrite_masked(system, src)
    print("  output:  %s" % out.text)
    print("  PASS - no invented numbers")
except HallucinatedNumber as e:
    print("  BLOCKED - %s" % e)
