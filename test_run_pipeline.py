#!/usr/bin/env python3
"""Self-check for run_pipeline.py's template filling and retry loop.

No subprocess/model call involved — run_agent_fn/run_visual_fn are faked, same
pattern as test_qwen_agent.py's call_model monkeypatching.
Run: python3 test_run_pipeline.py
"""
import tempfile
from pathlib import Path

from run_pipeline import fill_template, run_pipeline


def demo():
    # fill_template: real content-population template substitutes cleanly
    tmpl = Path(__file__).parent / "templates" / "content-population.txt"
    filled = fill_template(tmpl, {
        "task_name": "test task", "output_file": "out.html",
        "design_tokens": "bg: #fff", "content_fields": "name: Alice",
        "skeleton_html": "<div>x</div>",
    })
    assert "test task" in filled and "Alice" in filled, filled

    # succeeds on the first attempt: run_agent_fn is called once, no retry text injected
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        calls = []

        def fake_agent(task_path):
            calls.append(task_path.read_text())
            return {"finished": True}

        result = run_pipeline(
            template_path=tmpl, variables={
                "task_name": "t", "output_file": "out.html", "design_tokens": "",
                "content_fields": "", "skeleton_html": "",
            },
            workdir=workdir, output_name="out.html",
            run_agent_fn=fake_agent, run_visual_fn=lambda: {"pass": True},
            visual_spec=None, max_retries=3,
        )
        assert result["passed"] is True and result["attempts"] == 1, result
        assert "ATTEMPT" not in calls[0], "first attempt should not contain retry text"

    # fails every attempt: exhausts max_retries, reports failure with reasons, and the
    # 2nd+ brief carries the previous attempt's specific failure reason forward
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        calls = []

        def fake_agent_always_fails(task_path):
            calls.append(task_path.read_text())
            return {"finished": False, "crashed": "syntax error"}

        result = run_pipeline(
            template_path=tmpl, variables={
                "task_name": "t", "output_file": "out.html", "design_tokens": "",
                "content_fields": "", "skeleton_html": "",
            },
            workdir=workdir, output_name="out.html",
            run_agent_fn=fake_agent_always_fails, run_visual_fn=lambda: {"pass": True},
            visual_spec=None, max_retries=3,
        )
        assert result["passed"] is False and result["attempts"] == 3, result
        assert result["output_file"] is None, result
        assert "syntax error" in calls[1], "2nd attempt's brief should carry the 1st failure's reason"

    # verify passes but the visual gate fails: pipeline must still report failure, not
    # treat qwen_agent's own finished=True as the last word (same trust rule as --verify-command)
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        result = run_pipeline(
            template_path=tmpl, variables={
                "task_name": "t", "output_file": "out.html", "design_tokens": "",
                "content_fields": "", "skeleton_html": "",
            },
            workdir=workdir, output_name="out.html",
            run_agent_fn=lambda task_path: {"finished": True},
            run_visual_fn=lambda: {"pass": False, "notes": "hero too tall"},
            visual_spec="spec.txt", max_retries=1,
        )
        assert result["passed"] is False, result
        assert "hero too tall" in result["history"][0]["reason"], result

    print("all checks passed")


if __name__ == "__main__":
    demo()
