"""Batched bullet rewriting: safeguards per line, with a stand-in model. Offline."""
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yaml
from app.core.jd_extract import JobDescription, Requirement
from app.core.latex_parser import parse
from app.core.llm import LLMError, LLMResult, mask_numbers, unmask_numbers
from app.core.tailor import plan, rewrite
from app.core.vocabulary import Vocabulary

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
cfg = yaml.safe_load(open(os.path.join(ROOT, "backend", "data", "config.yaml"),
                          encoding="utf-8"))
vocab = Vocabulary.load(os.path.join(ROOT, "backend", "data", "vocabulary.yaml"))
resume = parse(io.open(os.path.join(ROOT, "main.tex"), encoding="utf-8").read())
jd = JobDescription(raw="", requirements=[Requirement(
    text="Java", kind="required", keys=sorted(vocab.extract("Java Spring Boot Kafka PostgreSQL")))])
p = plan(resume, jd, vocab, cfg)
fails = 0


def check(cond, label):
    global fails
    print("  %s %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        fails += 1


class FakeLLM:
    """Echoes each bullet back, with deliberate faults in some of them."""

    def __init__(self, fail_batch=False):
        self.calls = 0
        self.fail_batch = fail_batch

    def complete(self, system, user, **kw):
        self.calls += 1
        if self.fail_batch:
            raise LLMError("simulated outage")
        items = re.findall(r"\[(b\d+)\]\nbullet: (.*)", user)
        all_ph = re.findall(r"<N\d+>", user)
        out = []
        for label, text in items:
            if label == "b2" and all_ph:
                own = re.findall(r"<N\d+>", text)
                other = next((x for x in all_ph if x not in own), None)
                text = text + " across " + other if other else text
            elif label == "b3":
                text = text.rstrip(".") + " for 37 teams."
            elif label == "b4":
                continue                              # dropped by the model
            out.append({"id": label, "text": text})
        return LLMResult(text=json.dumps({"bullets": out}), model="fake",
                         prompt_tokens=100, completion_tokens=100)

    def rewrite_masked(self, system, source, **kw):
        self.calls += 1
        masked, mapping = mask_numbers(source.split("\n")[1])
        return LLMResult(text=unmask_numbers(masked, mapping), model="fake")


n = len(p.bullets)
print("batched rewrite of %d bullets" % n)
llm = FakeLLM()
r = rewrite(p, jd, llm, vocab, cfg)
check(llm.calls == 1 and r.calls == 1, "one model call for all %d bullets" % n)
ids = [rb.bullet.id for rb in p.bullets]
skipped = dict(r.skipped)
check("another bullet" in skipped.get(ids[1], ""), "a number moved from another bullet is blocked")
check("wrote digits" in skipped.get(ids[2], ""), "a digit the model wrote itself is blocked")
check("not returned" in skipped.get(ids[3], ""), "a bullet the model dropped keeps its original")
others = [i for k, i in enumerate(ids) if k not in (1, 2, 3)]
check(all(i in r.rewritten for i in others), "every other bullet passes the per-line checks")
with_num = [rb for rb in p.bullets if re.search(r"\d", rb.bullet.text) and rb.bullet.id in r.rewritten]
check(all(re.findall(r"\d[\d,.]*", rb.bullet.text) == re.findall(r"\d[\d,.]*", r.rewritten[rb.bullet.id])
          for rb in with_num), "real numbers are restored in their own bullet (%d checked)" % len(with_num))

print("\nbatch call fails -> one call per bullet")
llm = FakeLLM(fail_batch=True)
r = rewrite(p, jd, llm, vocab, cfg)
check(r.warnings and "one bullet at a time" in r.warnings[0], "falls back and says so")
check(llm.calls == 1 + n and len(r.rewritten) == n, "%d single calls, all bullets rewritten" % n)

print("\nbatch_size setting")
small = dict(cfg, tailoring={"batch_size": 5})
llm = FakeLLM()
r = rewrite(p, jd, llm, vocab, small)
check(llm.calls == -(-n // 5), "batch_size 5 -> %d calls for %d bullets" % (-(-n // 5), n))

print("\nFAILED: %d" % fails if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
