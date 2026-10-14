"""Runs every t_*.py check script as a pytest test.

Each script is a self-contained, readable walkthrough that prints what it
checks and exits non-zero on failure, so this file only has to run it and
assert on the exit code - the failure output appears in the pytest report.

Scripts that need your own main.tex or job_agent.db are skipped (not failed)
when those files are absent, e.g. on a fresh clone.
"""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
HAS_RESUME = os.path.exists(os.path.join(ROOT, "main.tex"))
HAS_DB = os.path.exists(os.path.join(ROOT, "job_agent.db"))
HAS_TECTONIC = os.path.exists(os.path.join(ROOT, "tools", "tectonic", "tectonic.exe"))

# (script, needs main.tex, needs job_agent.db, needs tectonic, live)
SCRIPTS = [
    ("t_parse.py",     True,  False, False, False),
    ("t_vocab.py",     False, False, False, False),
    ("t_pagefit.py",   True,  False, False, False),
    ("t_compile.py",   True,  False, True,  False),
    ("t_kb.py",        True,  True,  False, False),
    ("t_batch.py",     True,  False, False, False),
    ("t_skills.py",    True,  True,  False, False),
    # model / network
    ("t_fetch.py",     False, False, False, True),
    ("t_llm.py",       False, False, False, True),
    ("t_score.py",     True,  False, False, True),
    ("t_tailor.py",    True,  False, False, True),
    ("t_render.py",    True,  False, False, True),
    ("t_stability.py", True,  False, False, True),
]


def _params():
    for name, resume, db, tex, live in SCRIPTS:
        marks = [pytest.mark.live] if live else []
        if resume and not HAS_RESUME:
            marks.append(pytest.mark.skip(reason="needs your main.tex"))
        if db and not HAS_DB:
            marks.append(pytest.mark.skip(reason="needs job_agent.db"))
        if tex and not HAS_TECTONIC:
            marks.append(pytest.mark.skip(reason="needs tools/tectonic"))
        yield pytest.param(name, marks=marks, id=name[:-3])


@pytest.mark.parametrize("script", list(_params()))
def test_script(script):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, os.path.join(HERE, script)],
                          cwd=ROOT, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=900)
    output = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, "%s failed:\n%s" % (script, output[-4000:])
