(function () {
  // Soften common automation fingerprints used by Turnstile heuristics.
  try {
    Object.defineProperty(Navigator.prototype, "webdriver", {
      get: () => undefined,
      configurable: true,
    });
  } catch (e) {}

  // Some CF checks look at chrome.runtime; keep a minimal stub when missing.
  try {
    if (!window.chrome) window.chrome = {};
    if (!window.chrome.runtime) window.chrome.runtime = {};
  } catch (e) {}

  // Periodic attempt to click visible Turnstile checkbox in nested frames/shadow.
  function clickTurnstile() {
    try {
      const inputs = document.querySelectorAll('input[type="checkbox"], input');
      for (const el of inputs) {
        try {
          const rect = el.getBoundingClientRect();
          if (rect.width > 0 && rect.height > 0 && rect.width < 80 && rect.height < 80) {
            el.click();
          }
        } catch (e) {}
      }
      const iframes = document.querySelectorAll('iframe[src*="turnstile"], iframe[src*="challenges.cloudflare"]');
      for (const f of iframes) {
        try { f.click(); } catch (e) {}
      }
    } catch (e) {}
  }
  setInterval(clickTurnstile, 1500);
})();
