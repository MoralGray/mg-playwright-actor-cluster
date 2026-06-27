from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from swarm.profile import Fingerprint as ProfileFingerprint

STEALTH_JS_PATH = Path(__file__).resolve().parent / "stealth.js"

DEFAULT_PLUGINS = [
    "PDF Viewer",
    "Chrome PDF Viewer",
    "Chromium PDF Viewer",
    "WebKit built-in PDF",
]

WEBDRIVER_JS = """\
try {
  Object.defineProperty(Navigator.prototype, "webdriver", {
    value: undefined,
    writable: true,
    configurable: true,
  });
} catch (e) {}
"""

CDC_JS = """\
try {
  const stripCdc = (target) => {
    if (!target) return;
    const names = Object.getOwnPropertyNames(target).filter((n) => n.indexOf("$cdc_") === 0);
    for (const n of names) {
      try { delete target[n]; } catch (e) {}
      try {
        Object.defineProperty(target, n, {
          value: undefined,
          writable: true,
          configurable: true,
        });
      } catch (e) {}
    }
  };
  stripCdc(window);
  stripCdc(document);
  stripCdc(Document.prototype);
  stripCdc(Navigator.prototype);
} catch (e) {}
"""

CHROME_JS = """\
try {
  if (!window.chrome) window.chrome = {};
  const app = {
    isInstalled: false,
    InstallState: { DISABLED: 0, INSTALLED: 1, NOT_INSTALLED: 2 },
    RunningState: { CANNOT_RUN: 0, READY_TO_RUN: 1, RUNNING: 2 },
    getDetails: function () { return null; },
    getIsInstalled: function () { return 0; },
  };
  const runtime = {
    OnInstalledReason: {
      CHROME_UPDATE: "chrome_update",
      INSTALL: "install",
      SHARED_MODULE_UPDATE: "shared_module_update",
    },
    OnRestartReason: { APP_UPDATE: "app_update", OS_UPDATE: "os_update", PERIODIC: "periodic" },
    PlatformArch: {
      ARM: "arm", ARM64: "arm64", MIPS: "mips", MIPS64: "mips64",
      X86_32: "x86-32", X86_64: "x86-64",
    },
    PlatformNaclArch: {
      ARM: "arm", MIPS: "mips", MIPS64: "mips64",
      X86_32: "x86-32", X86_64: "x86-64",
    },
    PlatformOs: {
      ANDROID: "android", CROS: "cros", LINUX: "linux",
      MAC: "mac", OPENBSD: "openbsd", WIN: "win",
    },
    PlatformV8Version: "",
    RequestUpdateCheckStatus: {
      NO_UPDATE: "no_update", THROTTLED: "throttled",
      UPDATE_AVAILABLE: "update_available",
    },
    connect: function () { return null; },
    sendMessage: function () {},
  };
  Object.defineProperty(window, "chrome", {
    value: {
      app: app,
      csi: function () { return { startE: 0, onloadT: 0, pageT: 0, tran: 0 }; },
      loadTimes: function () {
        return {
          commitLoadTime: 0,
          connectionInfo: "h2",
          finishDocumentLoadTime: 0,
          finishLoadTime: 0,
          firstPaintAfterLoadTime: 0,
          firstPaintTime: 0,
          navigationType: "Other",
          npnNegotiatedProtocol: "h2",
          requestTime: 0,
          startLoadTime: 0,
          wasAlternateProtocolAvailable: false,
          wasFetchedViaSpdy: true,
          wasNpnNegotiated: true,
        };
      },
      runtime: runtime,
    },
    writable: true,
    configurable: true,
  });
} catch (e) {}
"""

HARDWARE_JS = """\
try {
  Object.defineProperty(navigator, "hardwareConcurrency", {
    value: 8,
    writable: true,
    configurable: true,
  });
} catch (e) {}
try {
  Object.defineProperty(navigator, "deviceMemory", {
    value: 8,
    writable: true,
    configurable: true,
  });
} catch (e) {}
try {
  // Desktop Chrome on ethernet returns effectiveType "4g" with type
  // "ethernet"; the previous "wifi"+4g combo was flagged as a mobile
  // emulation tell by the leak audit.
  Object.defineProperty(navigator, "connection", {
    value: {
      effectiveType: "4g",
      downlink: 10,
      rtt: 50,
      saveData: false,
      downlinkMax: 10000,
      type: "ethernet",
    },
    writable: true,
    configurable: true,
  });
} catch (e) {}
"""

BLUETOOTH_JS = """\
try {
  Object.defineProperty(navigator, "bluetooth", {
    value: undefined,
    writable: true,
    configurable: true,
  });
} catch (e) {}
try {
  Object.defineProperty(navigator, "usb", {
    value: undefined,
    writable: true,
    configurable: true,
  });
} catch (e) {}
try {
  Object.defineProperty(navigator, "serial", {
    value: undefined,
    writable: true,
    configurable: true,
  });
} catch (e) {}
"""

OUTER_JS = """\
try {
  Object.defineProperty(window, "outerWidth", {
    get: () => window.innerWidth + 40,
    configurable: true,
  });
} catch (e) {}
"""

PLATFORM_LANGUAGE_JS = """\
try {
  Object.defineProperty(navigator, "platform", {
    get: () => fp.platform,
    configurable: true,
  });
} catch (e) {}
try {
  Object.defineProperty(navigator, "language", {
    get: () => fp.language,
    configurable: true,
  });
} catch (e) {}
try {
  // Real Chrome ships multiple languages (interface + fallback). A single
  // language array is a minimal-config / automation tell.
  const langs = (fp.languages && fp.languages.length) ? fp.languages : [fp.language, "en-US", "en"];
  Object.defineProperty(navigator, "languages", {
    get: () => langs,
    configurable: true,
  });
} catch (e) {}
try {
  // Headless Chrome leaves pdfViewerEnabled=false; real headed Chrome ships
  // the built-in PDF viewer enabled.
  Object.defineProperty(navigator, "pdfViewerEnabled", {
    get: () => true,
    configurable: true,
  });
} catch (e) {}
"""

USER_AGENT_JS = """\
try {
  Object.defineProperty(navigator, "userAgent", {
    get: () => fp.user_agent,
    configurable: true,
  });
} catch (e) {}
try {
  Object.defineProperty(navigator, "vendor", {
    get: () => "Google Inc.",
    configurable: true,
  });
} catch (e) {}
"""

PLUGINS_JS = """\
try {
  const fakePlugins = fp.plugins.map((name) => ({
    name: name,
    filename: name.toLowerCase().replace(/\\s+/g, "") + ".dll",
    description: name,
    length: 1,
  }));
  Object.defineProperty(navigator, "plugins", { get: () => fakePlugins, configurable: true });
  Object.defineProperty(navigator, "mimeTypes", {
    get: () => [
      { type: "application/pdf", suffixes: "pdf", description: "Portable Document Format" },
      { type: "text/pdf", suffixes: "pdf", description: "Portable Document Format" },
    ],
    configurable: true,
  });
} catch (e) {}
"""

SCREEN_JS = """\
try {
  Object.defineProperty(screen, "width", { get: () => fp.width, configurable: true });
  Object.defineProperty(screen, "height", { get: () => fp.height, configurable: true });
  Object.defineProperty(screen, "availWidth", { get: () => fp.width, configurable: true });
  Object.defineProperty(screen, "availHeight", { get: () => fp.height - 40, configurable: true });
  Object.defineProperty(screen, "colorDepth", { get: () => 24, configurable: true });
  Object.defineProperty(window, "devicePixelRatio", { get: () => 1, configurable: true });
} catch (e) {}
"""

WEBGL_JS = """\
try {
  const UNMASKED_VENDOR = 0x9245;
  const UNMASKED_RENDERER = 0x9246;
  const origGet1 = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function (p) {
    if (p === UNMASKED_VENDOR) return fp.webgl_vendor;
    if (p === UNMASKED_RENDERER) return fp.webgl_renderer;
    return origGet1.apply(this, arguments);
  };
  if (window.WebGL2RenderingContext) {
    const origGet2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function (p) {
      if (p === UNMASKED_VENDOR) return fp.webgl_vendor;
      if (p === UNMASKED_RENDERER) return fp.webgl_renderer;
      return origGet2.apply(this, arguments);
    };
  }
} catch (e) {}
"""

CANVAS_JS = """\
try {
  const origGetImage = CanvasRenderingContext2D.prototype.getImageData;
  CanvasRenderingContext2D.prototype.getImageData = function () {
    const img = origGetImage.apply(this, arguments);
    img.data[0] = (img.data[0] + fp.canvas_noise) & 0xff;
    return img;
  };
} catch (e) {}
"""

AUDIO_JS = """\
try {
  const Native = window.AudioContext || window.webkitAudioContext;
  if (Native) {
    function PatchedAudioContext() {
      const ctx = new Native(...arguments);
      try {
        Object.defineProperty(ctx, "sampleRate", {
          get: () => fp.audio_sample_rate,
        });
      } catch (e) {}
      return ctx;
    }
    PatchedAudioContext.prototype = Native.prototype;
    window.AudioContext = PatchedAudioContext;
    if (window.webkitAudioContext) window.webkitAudioContext = PatchedAudioContext;
  }
} catch (e) {}
"""

TIMEZONE_JS = """\
try {
  const origResolved = Intl.DateTimeFormat.prototype.resolvedOptions;
  Intl.DateTimeFormat.prototype.resolvedOptions = function () {
    const opts = origResolved.apply(this, arguments);
    opts.timeZone = fp.timezone;
    return opts;
  };
} catch (e) {}
"""

FONTS_JS = """\
try {
  window.__INSTALLED_FONTS__ = fp.fonts;
  if (document.fonts && document.fonts.check) {
    const origCheck = document.fonts.check;
    document.fonts.check = function (font, family) {
      if (fp.fonts.indexOf(family) !== -1) return true;
      return origCheck.apply(this, arguments);
    };
  }
} catch (e) {}
"""

PERMISSIONS_JS = """\
try {
  if (navigator.permissions && navigator.permissions.query) {
    const origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = function (desc) {
      if (desc && desc.name === "notifications") {
        return Promise.resolve({ state: "prompt", onchange: null });
      }
      return origQuery(desc);
    };
  }
} catch (e) {}
try {
  if (window.Notification) {
    Object.defineProperty(Notification, "permission", {
      get: () => "default",
      configurable: true,
    });
  }
} catch (e) {}
"""

WEBGL_DEPTH_JS = """\
try {
  const MAX_TEXTURE_SIZE = 0x0D33;
  const MAX_VIEWPORT_DIMS = 0x0D3A;
  const MAX_VERTEX_ATTRIBS = 0x8869;
  const MAX_VARYING_VECTORS = 0x8DFC;
  const MAX_VERTEX_UNIFORM_VECTORS = 0x8DFB;
  const MAX_FRAGMENT_UNIFORM_VECTORS = 0x8DFD;
  const ALIASED_LINE_WIDTH_RANGE = 0x846E;
  const ALIASED_POINT_SIZE_RANGE = 0x846D;
  const patchGetParam = (proto) => {
    const orig = proto.getParameter;
    proto.getParameter = function (p) {
      switch (p) {
        case MAX_TEXTURE_SIZE: return 16384;
        case MAX_VIEWPORT_DIMS: return [32767, 32767];
        case MAX_VERTEX_ATTRIBS: return 16;
        case MAX_VARYING_VECTORS: return 30;
        case MAX_VERTEX_UNIFORM_VECTORS: return 4095;
        case MAX_FRAGMENT_UNIFORM_VECTORS: return 1024;
        case ALIASED_LINE_WIDTH_RANGE: return [1, 1];
        case ALIASED_POINT_SIZE_RANGE: return [1, 1024];
      }
      return orig.apply(this, arguments);
    };
    const origSPF = proto.getShaderPrecisionFormat;
    proto.getShaderPrecisionFormat = function (shadertype, precisiontype) {
      const r = origSPF.apply(this, arguments);
      if (r) { r.rangeMin = 127; r.rangeMax = 127; r.precision = 23; }
      return r;
    };
    const origGSE = proto.getSupportedExtensions;
    proto.getSupportedExtensions = function () {
      const exts = origGSE.apply(this, arguments);
      return exts ? exts.sort() : exts;
    };
  };
  if (window.WebGLRenderingContext) patchGetParam(WebGLRenderingContext.prototype);
  if (window.WebGL2RenderingContext) patchGetParam(WebGL2RenderingContext.prototype);
} catch (e) {}
"""

CLIENTRECTS_JS = """\
try {
  const round2 = (v) => Math.round(v * 100) / 100;
  const origGetClientRects = Element.prototype.getClientRects;
  Element.prototype.getClientRects = function () {
    const rects = origGetClientRects.apply(this, arguments);
    const out = [];
    for (const r of rects) {
      out.push(new DOMRect(round2(r.x), round2(r.y), round2(r.width), round2(r.height)));
    }
    return out;
  };
} catch (e) {}
"""

SCREEN_POS_JS = """\
try {
  Object.defineProperty(window, "screenX", { value: 0, configurable: true });
  Object.defineProperty(window, "screenY", { value: 0, configurable: true });
  Object.defineProperty(screen, "availTop", { get: () => 0, configurable: true });
  Object.defineProperty(screen, "availLeft", { get: () => 0, configurable: true });
} catch (e) {}
"""

USER_AGENT_DATA_JS = """\
try {
  const ua = fp.user_agent;
  const m = ua.match(/Chrome\\/(\\d+)/);
  const major = m ? parseInt(m[1], 10) : 127;
  const fm = ua.match(/Chrome\\/(\\d+\\.\\d+\\.\\d+\\.\\d+)/);
  const fullVersion = fm ? fm[1] : major + ".0.0.0";
  const platformMap = { "Win32": "Windows", "MacIntel": "macOS", "Linux x86_64": "Linux" };
  const platform = platformMap[fp.platform] || "Windows";
  const platformVersion = platform === "Windows" ? "10.0.0" : "10.15.0";
  const brands = [
    { brand: "Google Chrome", version: String(major) },
    { brand: "Chromium", version: String(major) },
    { brand: "Not_A Brand", version: "24" },
  ];
  Object.defineProperty(navigator, "userAgentData", {
    get: () => ({
      brands: brands,
      mobile: false,
      platform: platform,
      getHighEntropyValues: function (hints) {
        return Promise.resolve({
          architecture: "x86_64",
          bitness: "64",
          brands: brands,
          fullVersionList: [
            { brand: "Google Chrome", version: fullVersion },
            { brand: "Chromium", version: fullVersion },
            { brand: "Not_A Brand", version: "24.0.0.0" },
          ],
          mobile: false,
          model: "",
          platform: platform,
          platformVersion: platformVersion,
          uaFullVersion: fullVersion,
        });
      },
      toJSON: function () {
        return { brands: brands, mobile: false, platform: platform };
      },
    }),
    configurable: true,
  });
} catch (e) {}
"""

VISIBILITY_JS = """\
try {
  Object.defineProperty(document, "visibilityState", {
    get: () => "visible",
    configurable: true,
  });
  Object.defineProperty(document, "hidden", { get: () => false, configurable: true });
  document.hasFocus = function () { return true; };
} catch (e) {}
"""

SUPPRESS_BATTERY_JS = """\
try {
  // Desktop Chrome on Windows without a battery exposes getBattery returning
  // undefined or a BatteryManager with charging=true, level=1.0. Chromium's
  // built-in mock may return level=0.96 which flags automation. Explicitly
  // suppress the API so it returns undefined.
  navigator.getBattery = undefined;
} catch (e) {}
"""

OVERRIDES: list[str] = [
    CDC_JS,
    WEBDRIVER_JS,
    CHROME_JS,
    PERMISSIONS_JS,
    PLATFORM_LANGUAGE_JS,
    USER_AGENT_JS,
    USER_AGENT_DATA_JS,
    PLUGINS_JS,
    SCREEN_JS,
    SCREEN_POS_JS,
    HARDWARE_JS,
    BLUETOOTH_JS,
    SUPPRESS_BATTERY_JS,
    OUTER_JS,
    WEBGL_JS,
    WEBGL_DEPTH_JS,
    CANVAS_JS,
    AUDIO_JS,
    CLIENTRECTS_JS,
    VISIBILITY_JS,
    TIMEZONE_JS,
    FONTS_JS,
]


@dataclass(frozen=True, slots=True)
class RuntimeFingerprint:
    width: int
    height: int
    user_agent: str
    platform: str
    language: str
    webgl_vendor: str
    webgl_renderer: str
    fonts: list[str]
    audio_sample_rate: int
    timezone: str
    plugins: list[str] = field(default_factory=lambda: list(DEFAULT_PLUGINS))
    canvas_noise: int = 0
    languages: list[str] = field(default_factory=lambda: ["ru-RU", "en-US", "en"])


def _stable_seed(profile_fp: ProfileFingerprint) -> int:
    """Derive a deterministic seed from the actor's stable fingerprint fields.

    Tying canvas_noise / audio jitter to this seed makes the fingerprint
    reproducible across sessions for the same actor instead of per-run
    random, which is a stability signal anti-bot SDKs detect.
    """
    import hashlib

    payload = "|".join(
        [
            profile_fp.user_agent,
            profile_fp.platform,
            profile_fp.language,
            profile_fp.webgl_vendor,
            profile_fp.webgl_renderer,
            profile_fp.timezone,
            ",".join(profile_fp.fonts),
        ]
    )
    return int(hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8], 16)


def apply_variance(
    profile_fp: ProfileFingerprint,
    rng: random.Random | None = None,
) -> RuntimeFingerprint:
    # When no RNG is supplied, derive a stable per-actor seed so canvas_noise
    # and audio jitter are reproducible across sessions for the same actor.
    rng = rng or random.Random(_stable_seed(profile_fp))
    off_x = profile_fp.screen.offset_x or 0
    off_y = profile_fp.screen.offset_y or 0
    # Screen resolution must match a real monitor exactly (1920x1080,
    # 1366x768, 2560x1440, ...). Per-run pixel jitter (1923x1081) is a
    # strong anti-bot signal because real Chrome reports the display's
    # native resolution verbatim. Only apply variance when the profile
    # explicitly opts in via non-zero offsets AND a rng_seed is passed
    # (deterministic tests); production runs use the exact resolution.
    if off_x or off_y:
        width = max(320, profile_fp.screen.width + rng.randint(-off_x, off_x))
        height = max(240, profile_fp.screen.height + rng.randint(-off_y, off_y))
    else:
        width = profile_fp.screen.width
        height = profile_fp.screen.height
    fonts = list(profile_fp.fonts)
    rng.shuffle(fonts)
    audio_sample_rate = profile_fp.audio_sample_rate
    canvas_noise = rng.randint(0, 3)
    return RuntimeFingerprint(
        width=width,
        height=height,
        user_agent=profile_fp.user_agent,
        platform=profile_fp.platform,
        language=profile_fp.language,
        webgl_vendor=profile_fp.webgl_vendor,
        webgl_renderer=profile_fp.webgl_renderer,
        fonts=fonts,
        audio_sample_rate=audio_sample_rate,
        timezone=profile_fp.timezone,
        canvas_noise=canvas_noise,
        languages=list(profile_fp.languages),
    )


def _payload(rf: RuntimeFingerprint) -> dict[str, object]:
    return {
        "width": rf.width,
        "height": rf.height,
        "user_agent": rf.user_agent,
        "platform": rf.platform,
        "language": rf.language,
        "webgl_vendor": rf.webgl_vendor,
        "webgl_renderer": rf.webgl_renderer,
        "fonts": rf.fonts,
        "audio_sample_rate": rf.audio_sample_rate,
        "timezone": rf.timezone,
        "plugins": rf.plugins,
        "canvas_noise": rf.canvas_noise,
        "languages": rf.languages,
    }


def assemble_overrides() -> str:
    body = "\n".join(OVERRIDES)
    return "const fp = window.__FINGERPRINT__;\n" + body


def build_init_script(rf: RuntimeFingerprint) -> str:
    assignment = "window.__FINGERPRINT__ = " + json.dumps(_payload(rf)) + ";\n"
    return assignment + "(function () {\n" + assemble_overrides() + "\n})();"


def load_stealth_js() -> str:
    return STEALTH_JS_PATH.read_text(encoding="utf-8")
