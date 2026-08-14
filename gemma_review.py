#!/usr/bin/env python3
"""Screenshot an HTML file/fragment via headless Chrome and ask a local vision
model (gemma-4-E2B by default) whether it matches a spec — the visual gate
that was missing entirely from the original text-only review pipeline.

Frontend/UI tasks only — non-visual tasks use --verify-command in
qwen_agent.py instead (real test/execution, not a screenshot).

Usage:
    python3 gemma_review.py --html predict-screen.fragment.html \
        --spec spec-criteria.txt --out review.json
"""
import argparse
import base64
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

WRAPPER = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;padding:0;width:{w}px;height:{h}px;overflow:hidden;background:#fff;}}
</style></head><body>{content}</body></html>"""


def render_screenshot(html_path: Path, width: int, height: int, out_png: Path):
    content = html_path.read_text(encoding="utf-8")
    if "<html" not in content.lower():
        content = WRAPPER.format(w=width, h=height, content=content)
        wrapped = out_png.with_suffix(".rendered.html")
        wrapped.write_text(content, encoding="utf-8")
        target = wrapped
    else:
        target = html_path
    subprocess.run(
        [CHROME_PATH, "--headless", "--disable-gpu", f"--screenshot={out_png}",
         f"--window-size={width},{height}", f"file://{target.resolve()}"],
        capture_output=True, timeout=30, check=True,
    )
    if not out_png.is_file():
        raise RuntimeError("Chrome headless did not produce a screenshot")


def call_vision_model(base_url: str, model: str, spec: str, png_path: Path, temperature: float) -> str:
    image_b64 = base64.b64encode(png_path.read_bytes()).decode("ascii")
    prompt = (
        "You are reviewing a rendered UI screenshot against a spec. "
        "Reply with ONLY a JSON object: {\"pass\": true|false, \"notes\": \"...\"}. "
        "\"notes\" must cite what you actually see (layout, position, contrast), not assumptions.\n\n"
        f"Spec/criteria:\n{spec}"
    )
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ],
        }],
    }
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["choices"][0]["message"].get("content", "")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--html", required=True, help="Path to the HTML file or fragment to review")
    ap.add_argument("--spec", required=True, help="Path to a text file with the visual criteria to check")
    ap.add_argument("--out", required=True, help="Where to write the JSON verdict")
    ap.add_argument("--model", default="google/gemma-4-e2b")
    ap.add_argument("--base-url", default="http://localhost:1234/v1")
    ap.add_argument("--width", type=int, default=390)
    ap.add_argument("--height", type=int, default=844)
    ap.add_argument("--temperature", type=float, default=0.2)
    args = ap.parse_args()

    html_path = Path(args.html)
    spec = Path(args.spec).read_text(encoding="utf-8")
    out_path = Path(args.out)

    with tempfile.TemporaryDirectory() as tmp:
        png_path = Path(tmp) / "screenshot.png"
        render_screenshot(html_path, args.width, args.height, png_path)
        # Keep the screenshot next to the verdict for human inspection —
        # this is the artifact nobody looked at last time.
        saved_png = out_path.with_suffix(".png")
        saved_png.write_bytes(png_path.read_bytes())
        raw = call_vision_model(args.base_url, args.model, spec, png_path, args.temperature)

    # Vision models reliably wrap JSON in a ```json ... ``` fence despite being
    # asked not to — strip it before parsing rather than fighting the prompt.
    unfenced = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.IGNORECASE)
    try:
        verdict = json.loads(unfenced)
        if not isinstance(verdict, dict) or "pass" not in verdict:
            raise ValueError("missing 'pass' key")
    except (json.JSONDecodeError, ValueError) as e:
        verdict = {"pass": False, "notes": f"model did not return valid verdict JSON ({e}); raw response below", "raw_response": raw}

    verdict["screenshot"] = str(saved_png)
    out_path.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    print(json.dumps(verdict, indent=2))
    sys.exit(0 if verdict.get("pass") else 1)


if __name__ == "__main__":
    main()
