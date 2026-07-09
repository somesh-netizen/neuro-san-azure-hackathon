"""
Real-browser end-to-end test for the Neuro-San UI (Playwright, Chromium).
==========================================================================
Unlike the HTTP load tests (frontend_test.py / hackathon_test.py), this drives a REAL
browser: it renders the Next.js app, runs its JavaScript, and exercises the actual
click-to-streamed-design flow a participant sees — the gap the HTTP tests can't cover.

This is FUNCTIONAL validation at LOW concurrency (each headless Chrome ~150-300MB), NOT a
load test. Run 5-30 concurrent sessions to confirm the rendered experience works; use the
HTTP tests for load.

Flow per session (based on the real UI DOM):
  goto URL  ->  [handle login if auth is on]  ->  click [aria-label=select-network-designer]
  ->  fill #user-input with a design prompt  ->  click [aria-label=Send]
  ->  wait for [aria-label=Stop] to appear (generating) then disappear (done)
  ->  assert a response rendered  ->  refine (fill + Send + wait)  ->  record timings.

Runs from your Mac against the public URL (also validates the real external DNS/TLS path).
Prereqs:  python3 -m venv ~/e2e-venv; source ~/e2e-venv/bin/activate;
          pip install playwright; playwright install chromium

Usage:
  python3 e2e_playwright.py --users 5                 # 5 concurrent real browsers, headless
  python3 e2e_playwright.py --users 3 --headed        # watch it happen
  python3 e2e_playwright.py --users 10 --turns 2      # design + 1 refinement each
"""

import argparse
import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

URL_DEFAULT   = "https://hackathon.evolution.ml"
SHOTS_DIR     = Path(__file__).parent / "reports" / "e2e_shots"
DESIGN_PROMPTS = [
    "Design an agent network for automated customer support ticket triage and routing.",
    "Design a multi-agent network that reviews pull requests and posts structured feedback.",
    "Design an agent network for onboarding new employees end to end.",
    "Design a multi-agent pipeline that monitors cloud costs and recommends optimisations.",
    "Design an agent network for processing and approving supplier invoices.",
]
REFINEMENTS = [
    "Add a human-in-the-loop approval step before any final action is taken.",
    "Add a compliance-check agent that validates every output against GDPR.",
    "Add an escalation agent that pages a manager when confidence is low.",
]

# Timeouts (ms)
T_PAGELOAD   = 60_000
T_STOP_START = 60_000        # generation should begin within 60s of Send
T_DESIGN     = 600_000       # a full design may take minutes under load; be generous


async def _maybe_click(page, selector, timeout=4000):
    """Click a locator if it shows up within `timeout`; return True if clicked."""
    try:
        loc = page.locator(selector).first
        await loc.wait_for(state="visible", timeout=timeout)
        await loc.click()
        return True
    except PWTimeout:
        return False


async def _one_turn(page, text, label):
    """Submit one message and wait for the design to finish. Returns dict with timings."""
    box = page.locator("#user-input")
    await box.wait_for(state="visible", timeout=T_PAGELOAD)
    await box.click()
    await box.fill(text)

    t0 = time.monotonic()
    send = page.get_by_label("Send").first
    await send.wait_for(state="visible", timeout=10_000)
    await send.click()

    # generation started when the Stop button appears...
    stop = page.get_by_label("Stop").first
    await stop.wait_for(state="visible", timeout=T_STOP_START)
    ttfr = time.monotonic() - t0        # time-to-first-response (generation began)

    # ...and finished when it goes away (replaced by Regenerate)
    await stop.wait_for(state="hidden", timeout=T_DESIGN)
    total = time.monotonic() - t0

    # success signal: a Regenerate control now exists (a completed response is present)
    done_ok = await page.get_by_label("Regenerate").first.is_visible()
    return {"turn": label, "ttfr_s": round(ttfr, 1), "total_s": round(total, 1), "ok": done_ok}


async def _session(browser, idx, args):
    """One participant: fresh browser context (isolated cookies/session)."""
    res = {"idx": idx, "ok": False, "turns": [], "error": None}
    ctx = await browser.new_context(ignore_https_errors=False,
                                    viewport={"width": 1280, "height": 900})
    page = await ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        await page.goto(args.url, wait_until="domcontentloaded", timeout=T_PAGELOAD)
        await page.wait_for_timeout(1500)  # let the SPA hydrate

        # If auth is ON a login page appears; we have no creds here, so detect and flag clearly.
        if await page.locator("input[type=password]").count() > 0:
            raise RuntimeError("Login page detected — auth is ON; this script has no "
                               "credentials (turn auth OFF, or add login handling).")

        # Enter the designer (overlay button when no network is selected; no-op if already in).
        await _maybe_click(page, '[aria-label="select-network-designer"]', timeout=8000)

        # Loop turns: first = design, rest = refinements. Bounded by --duration-min if set,
        # otherwise by --turns.
        s_start = time.monotonic()
        i = 0
        while True:
            is_design = (i == 0)
            text = (DESIGN_PROMPTS[idx % len(DESIGN_PROMPTS)] if is_design
                    else REFINEMENTS[(idx + i) % len(REFINEMENTS)])
            turn = await _one_turn(page, text, "design" if is_design else "refine")
            res["turns"].append(turn)
            if not turn["ok"]:
                raise RuntimeError(f"{turn['turn']} finished but no completed-response signal")
            i += 1
            if args.duration_min > 0:
                if time.monotonic() - s_start >= args.duration_min * 60:
                    break
                await page.wait_for_timeout(8000)   # brief think between turns
            elif i >= args.turns:
                break

        res["ok"] = True
    except Exception as e:      # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"
        if errors:
            res["error"] += f"  | console: {errors[:2]}"
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            await page.screenshot(path=str(SHOTS_DIR / f"fail_user{idx}.png"), full_page=True)
        except Exception:
            pass
    finally:
        await ctx.close()
    return res


async def main_async(args):
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n{'='*70}\n  BROWSER E2E TEST (Playwright / Chromium)\n{'='*70}")
    print(f"  URL         : {args.url}")
    print(f"  Sessions    : {args.users} concurrent real browsers ({'headed' if args.headed else 'headless'})")
    if args.duration_min > 0:
        print(f"  Duration    : {args.duration_min} min per session (loop design -> refine)")
    else:
        print(f"  Turns each  : {args.turns} (turn 1 = design, rest = refinements)")
    print(f"{'='*70}\n  launching...")

    t0 = time.monotonic()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not args.headed)
        results = await asyncio.gather(*[_session(browser, i, args) for i in range(args.users)])
        await browser.close()
    wall = time.monotonic() - t0

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    all_turns = [t for r in results for t in r["turns"]]
    designs = [t for t in all_turns if t["turn"] == "design"]

    def _p(vals, q):
        if not vals: return 0
        s = sorted(vals); return s[min(len(s)-1, int(q*len(s)))]

    print(f"\n{'='*70}\n  RESULT — browser e2e\n{'='*70}")
    refines = [t for t in all_turns if t["turn"] == "refine"]
    turns_ok = [t for t in all_turns if t["ok"]]
    print(f"  Sessions passed : {len(ok)}/{args.users}   wall {wall:.0f}s")
    print(f"  Turns completed : {len(turns_ok)}/{len(all_turns)} ok  "
          f"({len(designs)} designs, {len(refines)} refinements)")
    if designs:
        tt = [t["ttfr_s"] for t in designs]; tot = [t["total_s"] for t in designs]
        print(f"  Design turn — time-to-first-response: p50 {_p(tt,.5)}s  p95 {_p(tt,.95)}s")
        print(f"  Design turn — full completion       : p50 {_p(tot,.5)}s  p95 {_p(tot,.95)}s  max {max(tot)}s")
    if bad:
        print(f"  FAILURES ({len(bad)}):")
        for r in bad:
            print(f"    user {r['idx']}: {r['error']}")
        print(f"  Screenshots → {SHOTS_DIR}")
    print(f"{'='*70}")
    print("  PASS if: sessions passed == total, designs render & complete, no console pageerrors.\n")
    return 0 if not bad else 1


def parse_args():
    p = argparse.ArgumentParser(description="Real-browser e2e test for the Neuro-San UI")
    p.add_argument("--url",   default=URL_DEFAULT)
    p.add_argument("--users", default=5, type=int, help="concurrent real browsers (5-30)")
    p.add_argument("--turns", default=1, type=int, help="turns per session (used only if --duration-min=0)")
    p.add_argument("--duration-min", default=0, type=int,
                   help="keep each session designing/refining for N minutes (0 = use --turns)")
    p.add_argument("--headed", action="store_true", help="show the browser windows")
    return p.parse_args()


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main_async(parse_args())))
