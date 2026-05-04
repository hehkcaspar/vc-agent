"""End-to-end test for session-level changes.

Visual + functional verification of everything touched this session:
- Login gate (APP_PASSWORD=123456)
- Portfolio list / entity selection
- Facts tab (Team Info icon, Co-investors, FactProvenanceBadge labels)
- News tab (unverified URL pill)
- Initial Screening tab (Recompose button)
- EntityEditModal (Identity & deal, Founders, Key team, Team size sections)

Captures screenshots at each step + collects JS console errors for regression detection.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import (
    BrowserContext,
    ConsoleMessage,
    Page,
    expect,
    sync_playwright,
)

OUT = Path("/tmp/e2e_screens")
OUT.mkdir(exist_ok=True)
APP = "http://localhost:3000"
PASSWORD = "123456"


def log(msg: str) -> None:
    print(f"[e2e] {msg}", flush=True)


def collect_errors(page: Page) -> list[str]:
    errs: list[str] = []
    page.on(
        "console",
        lambda msg: (
            errs.append(f"[console.{msg.type}] {msg.text}")
            if msg.type in ("error", "warning")
            else None
        ),
    )
    page.on("pageerror", lambda exc: errs.append(f"[pageerror] {exc}"))
    return errs


def login_if_needed(page: Page) -> None:
    """The shared-password gate (.env APP_PASSWORD=123456). Renders a
    LoginGate component with a password input."""
    pw = page.locator('input[type="password"]').first
    if pw.count() > 0 and pw.is_visible():
        log("Login gate present → entering password")
        pw.fill(PASSWORD)
        # Find the submit button — could be type=submit or text 'Continue'
        for sel in ('button[type="submit"]', 'button:has-text("Continue")',
                    'button:has-text("Submit")', 'button:has-text("Enter")'):
            btn = page.locator(sel).first
            if btn.count() > 0:
                btn.click()
                break
        page.wait_for_load_state("networkidle")
        time.sleep(0.5)


def find_entity_link(page: Page, name: str) -> bool:
    """Return True if we successfully navigated to an entity by name."""
    # Portfolio entity rows are <Link> anchors; click the row containing the name.
    link = page.locator(f'a:has-text("{name}")').first
    if link.count() == 0:
        log(f"  ! entity {name!r} not visible")
        return False
    link.click()
    page.wait_for_load_state("networkidle")
    time.sleep(0.5)
    return True


def click_tab(page: Page, label: str) -> bool:
    """Click an EntityDetail tab by label (Workroom, Facts, etc.)."""
    tab = page.get_by_role("tab", name=label).first
    if tab.count() == 0:
        # Fallback: text match on the tab list
        tab = page.locator(f'[role="tablist"] >> text={label}').first
    if tab.count() == 0:
        log(f"  ! tab {label!r} not found")
        return False
    tab.click()
    page.wait_for_load_state("networkidle")
    time.sleep(0.6)
    return True


def shot(page: Page, name: str) -> None:
    p = OUT / f"{name}.png"
    page.screenshot(path=str(p), full_page=False)
    log(f"  📸 {p}")


def test_facts_tab(page: Page, results: dict) -> None:
    log("FACTS tab")
    if not click_tab(page, "Facts"):
        results["facts_tab"] = "missing"
        return
    shot(page, "facts")

    # CS6: Info icon in Team header
    team_info = page.locator('.facts-section-info').first
    if team_info.count() > 0:
        results["team_info_icon"] = "present"
        # Hover to verify tooltip text via title attr
        title = team_info.get_attribute("title") or ""
        if "uploaded documents" in title.lower():
            results["team_info_tooltip"] = "ok"
        else:
            results["team_info_tooltip"] = f"unexpected: {title[:60]}"
    else:
        results["team_info_icon"] = "missing"

    # CS5: Co-investors section
    coinv = page.locator('.facts-coinvestors').first
    if coinv.count() > 0:
        results["coinvestors_section"] = "present"
        chips = page.locator('.facts-coinvestor-chip').all()
        linked = [c for c in chips if "facts-coinvestor-chip--linked" in (c.get_attribute("class") or "")]
        results["coinvestor_chips"] = len(chips)
        results["coinvestor_linked_chips"] = len(linked)
    else:
        results["coinvestors_section"] = "missing-or-empty"

    # FactProvenanceBadge — hover one to confirm new title text
    badge = page.locator('.fact-provenance-trigger').first
    if badge.count() > 0:
        title = badge.get_attribute("title") or ""
        results["provenance_badge"] = "present"
        results["provenance_badge_title"] = title[:80]
    else:
        results["provenance_badge"] = "absent (no facts have ledger entries)"


def test_edit_modal(page: Page, results: dict) -> None:
    log("EDIT modal")
    # Find and click any Pencil edit button
    # In EntityFactsTab the edit button is on Our positions / via header
    # We'll use the lucide Pencil icon which has a specific path.
    # The button has class 'facts-section-edit' or similar — try by aria/label.
    pencil = page.locator('button[title*="Edit"]').first
    if pencil.count() == 0:
        pencil = page.locator('.facts-section-edit:has-text("Edit")').first
    if pencil.count() == 0:
        # Fallback: any button containing the lucide Pencil svg
        pencil = page.locator('button:has(svg.lucide-pencil)').first

    if pencil.count() == 0:
        log("  ! Edit pencil not found; skipping modal test")
        results["edit_modal"] = "trigger-missing"
        return

    pencil.click()
    time.sleep(0.6)
    page.wait_for_load_state("networkidle")

    modal = page.locator('.modal').first
    if modal.count() == 0:
        results["edit_modal"] = "did-not-open"
        return
    shot(page, "edit_modal")
    results["edit_modal"] = "open"

    # CS3: check section presence by label text
    sections_to_check = [
        ("Deal stage", "deal_stage_section"),
        ("Identity", "identity_section"),
        ("Founders", "founders_section"),
        ("Key team", "key_team_section"),
        ("Team size", "team_size_section"),
        ("Our positions", "positions_section"),
    ]
    for label, key in sections_to_check:
        sec = modal.locator(f'.entity-edit-section-title:has-text("{label}")').first
        results[key] = "present" if sec.count() > 0 else "MISSING"

    # Check for the specific new fields in Identity section
    for field_label in ("Referral source", "One-liner", "Description",
                        "Business model", "HQ location", "Industry tags"):
        f = modal.locator(f'label:has-text("{field_label}")').first
        results[f"identity_field_{field_label.lower().replace(' ', '_')}"] = (
            "present" if f.count() > 0 else "MISSING"
        )

    # Close modal
    cancel = modal.locator('button:has-text("Cancel")').first
    if cancel.count() > 0:
        cancel.click()
        time.sleep(0.4)


def test_news_tab(page: Page, results: dict) -> None:
    log("NEWS tab")
    if not click_tab(page, "News"):
        results["news_tab"] = "missing"
        return
    shot(page, "news")
    rows = page.locator('.entity-news-row').all()
    results["news_rows"] = len(rows)
    # CS4: unverified pill on at least one
    unverified = page.locator('.entity-news-url-status').all()
    results["news_unverified_pills"] = len(unverified)
    if unverified:
        first_title = unverified[0].get_attribute("title") or ""
        results["news_unverified_first_title"] = first_title[:80]


def test_initial_screening_tab(page: Page, results: dict) -> None:
    log("INITIAL SCREENING tab")
    # The IS tab only shows when memo exists; check for both v1 + v2 labels.
    for label in ("Initial Screening", "Screening v1", "Screening v2"):
        if click_tab(page, label):
            shot(page, f"is_{label.replace(' ', '_').lower()}")
            # Check for Recompose button (CS3)
            recompose = page.locator('button:has-text("Recompose")').first
            if recompose.count() > 0:
                results[f"recompose_button_{label}"] = "present"
                results["recompose_button_title"] = (
                    recompose.get_attribute("title") or ""
                )[:80]
            else:
                results[f"recompose_button_{label}"] = "missing"
            # 2026-05-03 inline editor — Surface 2 per-section Edit
            section_edit_btns = page.locator(
                '.screening-section-header button:has-text("Edit")'
            )
            results["is_section_edit_buttons"] = section_edit_btns.count()
            return
    results["is_tab"] = "no-IS-tab-visible (no memo for entity)"


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context: BrowserContext = browser.new_context(
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=True,
        )
        page = context.new_page()
        errors = collect_errors(page)
        results: dict[str, object] = {}

        log("→ navigating to home")
        page.goto(APP)
        page.wait_for_load_state("networkidle")
        time.sleep(1)
        shot(page, "00_home_or_login")

        login_if_needed(page)
        page.wait_for_load_state("networkidle")
        time.sleep(0.5)
        shot(page, "01_after_login")

        # Should be on /portfolio after login
        log("→ portfolio list")
        # Navigate explicitly
        page.goto(f"{APP}/portfolio")
        page.wait_for_load_state("networkidle")
        time.sleep(0.5)
        shot(page, "02_portfolio")

        # Pick an entity that has news + facts data so the new pill / co-investor
        # / Recompose UI all have something to render against. Override Co has
        # 9 news records (some with _url_status != verified — perfect for pill
        # test). Falls back to Glacian / any.
        log("→ opening Override Co (has news data with mixed url_status)")
        opened = False
        for candidate in ("Override Co", "Glacian", "Linewise", "SceniX"):
            if find_entity_link(page, candidate):
                log(f"  ✓ opened {candidate}")
                opened = True
                break
        if not opened:
            any_link = page.locator('a[href*="/portfolio/entities/"]').first
            if any_link.count() == 0:
                results["error"] = "no entities visible on portfolio list"
                browser.close()
                _report(results, errors)
                return 1
            any_link.click()
            page.wait_for_load_state("networkidle")
        shot(page, "03_entity_landing")

        test_facts_tab(page, results)
        test_news_tab(page, results)
        test_initial_screening_tab(page, results)

        # Click back to Facts to find the Edit pencil
        click_tab(page, "Facts")
        time.sleep(0.4)
        test_edit_modal(page, results)

        browser.close()
        _report(results, errors)
        return 0


def _report(results: dict, errors: list[str]) -> None:
    print()
    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    for k, v in results.items():
        marker = "❌" if v == "MISSING" or (isinstance(v, str) and v.startswith("missing")) else "  "
        print(f"  {marker} {k}: {v}")
    print()
    print("=" * 70)
    print(f"JS CONSOLE ISSUES: {len(errors)}")
    print("=" * 70)
    if errors:
        # Filter known noise (e.g. resource 404s for assets we can't help)
        for e in errors[:30]:
            print(f"  • {e}")
        if len(errors) > 30:
            print(f"  ... and {len(errors) - 30} more")
    print()
    print(f"Screenshots in {OUT}")


if __name__ == "__main__":
    sys.exit(main())
