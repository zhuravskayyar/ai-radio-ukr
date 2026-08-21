"""Render both Vector Radio UI themes and exercise the theme controls."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/ui/index.html"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def visible(page, selector: str) -> bool:
    return page.locator(selector).evaluate(
        "element => { const box = element.getBoundingClientRect(); "
        "return box.width > 0 && box.height > 0; }"
    )


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        headless=True,
        executable_path=str(EDGE) if EDGE.is_file() else None,
    )
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    console_errors: list[str] = []
    page.on("console", lambda message: (
        console_errors.append(message.text) if message.type == "error" else None
    ))
    page.goto(BASE_URL, wait_until="networkidle")

    assert page.locator("body").get_attribute("data-ui-theme") == "vector"
    assert visible(page, ".hero")
    assert not visible(page, "#boomboxInterface")

    page.get_by_role("button", name="Налаштування", exact=True).click()
    page.locator('[data-theme-choice="boombox"]').click()
    assert page.locator("body").get_attribute("data-ui-theme") == "boombox"
    assert page.evaluate("localStorage.getItem('vector-radio-ui-theme')") == "boombox"

    page.get_by_role("button", name="Головний екран", exact=True).click()
    assert visible(page, "#boomboxInterface")
    assert not visible(page, ".hero")
    assert page.locator("#boomboxPresets button").count() == 5

    page.evaluate("""syncBoomboxTrack({
        id: 101,
        artist: 'Panic! at the Disco',
        title: 'I Write Sins Not Tragedies',
        youtube_id: '_lAVo-c6uh8'
    })""")
    assert "_lAVo-c6uh8" in page.locator("#boomboxCassetteCoverImage").get_attribute("data-cover-key")
    page.evaluate("""state.index = 1; syncBoomboxTrack({
        id: 102,
        artist: 'Fall Out Boy',
        title: \"Sugar, We're Goin Down\",
        youtube_id: 'uhG-vLZrb-g',
        cover_path: 'cache/covers/uhG-vLZrb-g.jpg'
    })""")
    assert "cassette-changing" in (page.locator("#boomboxCassette").get_attribute("class") or "")
    assert "cassette-swapping" in (page.locator("#boomboxPlayer").get_attribute("class") or "")
    page.wait_for_timeout(920)
    assert page.locator("#boomboxCassetteTitle").text_content() == "Sugar, We're Goin Down"
    assert "cache/covers/uhG-vLZrb-g.jpg" in page.locator("#boomboxCassetteCoverImage").get_attribute("data-cover-key")
    assert "cassette-swapping" not in (page.locator("#boomboxPlayer").get_attribute("class") or "")

    page.locator('[data-boombox-source="tape"]').click()
    assert page.locator("#boomboxSourceLabel").inner_text() == "TAPE"
    assert page.locator("#boomboxBandLabel").inner_text() == "DECK"
    page.locator('[data-boombox-source="bluetooth"]').click()
    assert page.locator("#boomboxSourceLabel").inner_text() == "BLUETOOTH"
    assert page.locator("#boomboxFrequency").get_attribute("aria-label") == "READY"
    page.locator('[data-boombox-source="radio"]').click()
    assert page.locator("#boomboxSourceLabel").inner_text() == "RADIO"
    page.locator("#boomboxVolume").fill("42")
    assert page.locator("#volume").input_value() == "42"
    assert page.locator("#boomboxVolumeValue").inner_text() == "42%"
    page.locator("#boomboxVolume").fill("75")
    page.locator(".boombox-volume-wrap").dispatch_event("wheel", {"deltaY": -100})
    assert page.locator("#boomboxVolumeValue").inner_text() == "78%"
    page.locator("#boomboxVolume").fill("75")
    page.locator("#boomboxEject").click()
    assert "cassette-open" in (page.locator("#boomboxPlayer").get_attribute("class") or "")
    page.locator("#boomboxEject").click()
    assert "cassette-open" not in (page.locator("#boomboxPlayer").get_attribute("class") or "")
    page.locator('[data-boombox-source="radio"]').click()
    page.evaluate("state.playing = true; syncPlaybackUi()")
    page.wait_for_timeout(180)
    assert "is-playing" in (page.locator("#boomboxPlayer").get_attribute("class") or "")
    assert "active" in (page.locator("#boomboxSignalLed").get_attribute("class") or "")
    assert page.locator(".boombox-equalizer i").evaluate_all(
        "bars => bars.some(bar => parseFloat(bar.style.height) > 20)"
    )

    boombox_preview = ROOT / "boombox-theme-preview.png"
    page.screenshot(path=str(boombox_preview), full_page=True)

    page.set_viewport_size({"width": 820, "height": 680})
    page.reload(wait_until="networkidle")
    assert page.locator("body").get_attribute("data-ui-theme") == "boombox"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    page.get_by_role("button", name="Налаштування", exact=True).click()
    page.locator('[data-theme-choice="vector"]').click()
    page.get_by_role("button", name="Головний екран", exact=True).click()
    assert page.locator("body").get_attribute("data-ui-theme") == "vector"
    assert visible(page, ".hero")
    assert not visible(page, "#boomboxInterface")

    vector_preview = ROOT / "vector-theme-preview.png"
    page.screenshot(path=str(vector_preview), full_page=True)
    browser.close()

print(json.dumps({
    "ok": True,
    "boombox_preview": str(boombox_preview),
    "vector_preview": str(vector_preview),
    "console_errors": console_errors,
}, ensure_ascii=False, indent=2))
