#!/usr/bin/env python3
"""Self-check for gemma_review.py's render + verdict-parsing logic.

No model call — exercises render_screenshot() (real headless Chrome) and the
JSON-fallback parsing path directly. Run: python3 test_gemma_review.py
"""
import json
import re
import tempfile
from pathlib import Path

from gemma_review import render_screenshot


def demo():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        fragment = tmp / "frag.html"
        fragment.write_text('<div style="background:#000;color:#fff;">hi</div>', encoding="utf-8")
        png = tmp / "shot.png"
        render_screenshot(fragment, 390, 844, png)
        assert png.is_file() and png.stat().st_size > 0, "no screenshot produced"

        wrapped = png.with_suffix(".rendered.html")
        assert wrapped.is_file(), "fragment should get wrapped before rendering"
        assert "width:390px" in wrapped.read_text()

    # Verdict fallback parsing (same logic as main(), inlined so it's testable
    # without a live model call)
    def parse(raw):
        unfenced = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.IGNORECASE)
        try:
            v = json.loads(unfenced)
            if not isinstance(v, dict) or "pass" not in v:
                raise ValueError("missing 'pass' key")
            return v
        except (json.JSONDecodeError, ValueError):
            return {"pass": False}

    assert parse('{"pass": true, "notes": "looks fine"}')["pass"] is True
    assert parse("not json at all")["pass"] is False
    assert parse('{"notes": "no pass key"}')["pass"] is False
    assert parse('```json\n{"pass": true, "notes": "fenced"}\n```')["pass"] is True

    print("all checks passed")


if __name__ == "__main__":
    demo()
