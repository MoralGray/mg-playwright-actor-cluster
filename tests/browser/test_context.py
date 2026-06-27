from __future__ import annotations

import asyncio
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from playwright.async_api import async_playwright

from browser.context import create_context
from browser.fingerprint import (
    apply_variance,
    build_init_script,
    load_stealth_js,
)
from swarm.profile import load_actor

ACTORS_DIR = Path(__file__).resolve().parent.parent.parent / "configs" / "actors"


class TestApplyVariance(unittest.TestCase):
    def _run(self, coro: object) -> object:
        return asyncio.run(coro)  # type: ignore[arg-type]

    def test_deterministic_with_seed(self) -> None:
        fp = load_actor("wildberries_user_deterministic", ACTORS_DIR).fingerprint
        a = apply_variance(fp, random.Random(42))
        b = apply_variance(fp, random.Random(42))
        self.assertEqual(a, b)

    def test_differs_across_seeds(self) -> None:
        fp = load_actor("wildberries_user_deterministic", ACTORS_DIR).fingerprint
        a = apply_variance(fp, random.Random(1))
        b = apply_variance(fp, random.Random(2))
        differs = (
            a.width != b.width
            or a.height != b.height
            or a.canvas_noise != b.canvas_noise
            or a.audio_sample_rate != b.audio_sample_rate
            or a.fonts != b.fonts
        )
        self.assertTrue(differs)

    def test_variance_within_offset_bounds(self) -> None:
        fp = load_actor("wildberries_user_deterministic", ACTORS_DIR).fingerprint
        for seed in range(20):
            rf = apply_variance(fp, random.Random(seed))
            self.assertGreaterEqual(rf.width, fp.screen.width - fp.screen.offset_x)
            self.assertLessEqual(rf.width, fp.screen.width + fp.screen.offset_x)
            self.assertGreaterEqual(rf.height, fp.screen.height - fp.screen.offset_y)
            self.assertLessEqual(rf.height, fp.screen.height + fp.screen.offset_y)

    def test_fonts_preserve_membership(self) -> None:
        fp = load_actor("wildberries_user_deterministic", ACTORS_DIR).fingerprint
        rf = apply_variance(fp, random.Random(7))
        self.assertEqual(set(rf.fonts), set(fp.fonts))


class TestInitScript(unittest.TestCase):
    def test_contains_overrides(self) -> None:
        fp = load_actor("wildberries_user_deterministic", ACTORS_DIR).fingerprint
        rf = apply_variance(fp, random.Random(0))
        script = build_init_script(rf)
        self.assertIn("navigator", script)
        self.assertIn("webdriver", script)
        self.assertIn("webgl_vendor", script)
        self.assertIn("audio_sample_rate", script)
        self.assertIn(rf.timezone, script)
        self.assertIn(rf.user_agent, script)

    def test_payload_injected(self) -> None:
        fp = load_actor("wildberries_user_llm", ACTORS_DIR).fingerprint
        rf = apply_variance(fp, random.Random(0))
        script = build_init_script(rf)
        self.assertIn(f'"width": {rf.width}', script)
        self.assertIn(f'"height": {rf.height}', script)

    def test_stealth_js_is_static_mirror(self) -> None:
        static = load_stealth_js()
        self.assertIn("__FINGERPRINT__", static)
        self.assertIn("webdriver", static)
        self.assertIn("UNMASKED_VENDOR", static)

    def test_new_overrides_present(self) -> None:
        fp = load_actor("wildberries_user_deterministic", ACTORS_DIR).fingerprint
        rf = apply_variance(fp, random.Random(0))
        script = build_init_script(rf)
        self.assertIn("$cdc_", script)
        self.assertIn("window.chrome", script)
        self.assertIn("hardwareConcurrency", script)
        self.assertIn("deviceMemory", script)
        self.assertIn("effectiveType", script)
        self.assertIn("bluetooth", script)
        self.assertIn("outerWidth", script)


class TestCreateContext(unittest.TestCase):
    def _run(self, coro: object) -> object:
        return asyncio.run(coro)  # type: ignore[arg-type]

    def test_webdriver_undefined(self) -> None:
        async def scenario() -> object:
            profile = load_actor("wildberries_user_deterministic", ACTORS_DIR)
            async with async_playwright() as pw:
                browser, context, rf, _cp = await create_context(
                    pw, profile, headless=True, rng_seed=1
                )
                try:
                    page = await context.new_page()
                    await page.goto("about:blank")
                    webdriver = await page.evaluate("navigator.webdriver")
                    ua = await page.evaluate("navigator.userAgent")
                    return webdriver, ua, rf.user_agent
                finally:
                    await context.close()
                    await browser.close()

        webdriver, ua, expected_ua = self._run(scenario())  # type: ignore[misc]
        self.assertIsNone(webdriver)
        self.assertEqual(ua, expected_ua)

    def test_webgl_and_timezone_match(self) -> None:
        async def scenario() -> object:
            profile = load_actor("wildberries_user_llm", ACTORS_DIR)
            async with async_playwright() as pw:
                browser, context, rf, _cp = await create_context(
                    pw, profile, headless=True, rng_seed=3
                )
                try:
                    page = await context.new_page()
                    await page.goto("about:blank")
                    webgl = await page.evaluate(
                        """() => {
                            const c = document.createElement('canvas');
                            const gl = c.getContext('webgl');
                            const ext = gl.getExtension('WEBGL_debug_renderer_info');
                            return [
                                gl.getParameter(ext.UNMASKED_VENDOR_WEBGL),
                                gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)
                            ];
                        }"""
                    )
                    tz = await page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
                    lang = await page.evaluate("navigator.language")
                    return webgl, tz, lang, rf
                finally:
                    await context.close()
                    await browser.close()

        webgl, tz, lang, rf = self._run(scenario())  # type: ignore[misc]
        self.assertEqual(webgl, [rf.webgl_vendor, rf.webgl_renderer])
        self.assertEqual(tz, rf.timezone)
        self.assertEqual(lang, rf.language)

    def test_viewport_matches_runtime_fingerprint(self) -> None:
        async def scenario() -> object:
            profile = load_actor("wildberries_user_deterministic", ACTORS_DIR)
            async with async_playwright() as pw:
                browser, context, rf, _cp = await create_context(
                    pw, profile, headless=True, rng_seed=5
                )
                try:
                    page = await context.new_page()
                    await page.goto("about:blank")
                    size = await page.evaluate("[window.innerWidth, window.innerHeight]")
                    return size, (rf.width, rf.height)
                finally:
                    await context.close()
                    await browser.close()

        size, expected = self._run(scenario())  # type: ignore[misc]
        self.assertEqual(size, list(expected))

    def test_stealth_overrides_applied(self) -> None:
        async def scenario() -> object:
            profile = load_actor("wildberries_user_deterministic", ACTORS_DIR)
            async with async_playwright() as pw:
                browser, context, _rf, _cp = await create_context(
                    pw, profile, headless=True, rng_seed=11
                )
                try:
                    page = await context.new_page()
                    await page.goto("about:blank")
                    return await page.evaluate(
                        """() => {
                            const webdriverDesc = Object.getOwnPropertyDescriptor(
                                Navigator.prototype, "webdriver"
                            );
                            return {
                                webdriver: navigator.webdriver,
                                webdriverHasValue: webdriverDesc
                                    ? Object.prototype.hasOwnProperty.call(webdriverDesc, "value")
                                    : false,
                                webdriverHasGetter: webdriverDesc
                                    ? Object.prototype.hasOwnProperty.call(webdriverDesc, "get")
                                    : false,
                                chromeApp: !!(window.chrome && window.chrome.app),
                                chromeLoadTimes: typeof (window.chrome && window.chrome.loadTimes),
                                chromeRuntime: !!(window.chrome && window.chrome.runtime),
                                hardwareConcurrency: navigator.hardwareConcurrency,
                                deviceMemory: navigator.deviceMemory,
                                connection: navigator.connection
                                    ? navigator.connection.effectiveType
                                    : null,
                                bluetooth: navigator.bluetooth,
                                usb: navigator.usb,
                                serial: navigator.serial,
                                outerWidth: window.outerWidth,
                                innerWidth: window.innerWidth,
                                cdcAsField: window.hasOwnProperty("$cdc_anything")
                                    ? "present"
                                    : "absent",
                            };
                        }"""
                    )
                finally:
                    await context.close()
                    await browser.close()

        res = self._run(scenario())  # type: ignore[misc]
        self.assertIsNone(res["webdriver"])
        self.assertTrue(res["webdriverHasValue"])
        self.assertFalse(res["webdriverHasGetter"])
        self.assertTrue(res["chromeApp"])
        self.assertEqual(res["chromeLoadTimes"], "function")
        self.assertTrue(res["chromeRuntime"])
        self.assertEqual(res["hardwareConcurrency"], 8)
        self.assertEqual(res["deviceMemory"], 8)
        self.assertEqual(res["connection"], "4g")
        self.assertIsNone(res["bluetooth"])
        self.assertIsNone(res["usb"])
        self.assertIsNone(res["serial"])
        self.assertEqual(res["outerWidth"], res["innerWidth"] + 40)
        self.assertEqual(res["cdcAsField"], "absent")

    def test_extended_stealth_overrides(self) -> None:
        async def scenario() -> object:
            profile = load_actor("wildberries_user_deterministic", ACTORS_DIR)
            async with async_playwright() as pw:
                browser, context, _rf, _cp = await create_context(
                    pw, profile, headless=True, rng_seed=21
                )
                try:
                    page = await context.new_page()
                    await page.goto("about:blank")
                    return await page.evaluate(
                        """async () => {
                            const perm = await navigator.permissions.query({name:"notifications"});
                            const uaData = navigator.userAgentData
                                ? await navigator.userAgentData.getHighEntropyValues([
                                    "platform","architecture","bitness","fullVersionList"])
                                : null;
                            const gl = document.createElement("canvas").getContext("webgl");
                            const glPerf = await new Promise(r => {
                                const s = gl.getShaderPrecisionFormat(
                                    gl.FRAGMENT_SHADER, gl.HIGH_FLOAT);
                                r({rangeMin: s.rangeMin, precision: s.precision});
                            });
                            const rect = document.body.getClientRects();
                            const battery = await navigator.getBattery();
                            return {
                                notificationPermission: Notification.permission,
                                permState: perm.state,
                                uaBrands: navigator.userAgentData
                                    ? navigator.userAgentData.brands.length : 0,
                                uaPlatform: uaData ? uaData.platform : null,
                                uaArchitecture: uaData ? uaData.architecture : null,
                                maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE),
                                shaderRangeMin: glPerf.rangeMin,
                                shaderPrecision: glPerf.precision,
                                clientRectFractional: rect.length
                                    ? String(rect[0].x).indexOf(".") !== -1 : false,
                                screenX: window.screenX,
                                availTop: screen.availTop,
                                visibilityState: document.visibilityState,
                                hidden: document.hidden,
                                hasFocus: document.hasFocus(),
                                batteryLevel: battery.level,
                                batteryCharging: battery.charging,
                            };
                        }"""
                    )
                finally:
                    await context.close()
                    await browser.close()

        res = self._run(scenario())  # type: ignore[misc]
        self.assertEqual(res["notificationPermission"], "default")
        self.assertEqual(res["permState"], "prompt")
        self.assertGreaterEqual(res["uaBrands"], 3)
        self.assertEqual(res["uaArchitecture"], "x86")
        self.assertEqual(res["maxTextureSize"], 16384)
        self.assertEqual(res["shaderRangeMin"], 127)
        self.assertEqual(res["shaderPrecision"], 23)
        self.assertEqual(res["screenX"], 0)
        self.assertEqual(res["availTop"], 0)
        self.assertEqual(res["visibilityState"], "visible")
        self.assertFalse(res["hidden"])
        self.assertTrue(res["hasFocus"])
        self.assertEqual(res["batteryLevel"], 1.0)
        self.assertTrue(res["batteryCharging"])

    def test_per_actor_seed_is_stable(self) -> None:
        fp = load_actor("wildberries_user_deterministic", ACTORS_DIR).fingerprint
        a = apply_variance(fp)
        b = apply_variance(fp)
        self.assertEqual(a.canvas_noise, b.canvas_noise)
        self.assertEqual(a.audio_sample_rate, b.audio_sample_rate)


if __name__ == "__main__":
    unittest.main()
