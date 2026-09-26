// Run with: node --test tests/test_dashboard_auth.cjs
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

const source = readFileSync(join(__dirname, "../src/chatgpt_web2api/dashboard/app.js"), "utf8");
const storageKey = "web2api.stats.auth.v1";
const sevenDays = 7 * 86400000;
const now = 1800000000000;
const report = {
  summary: { total: 0, success_rate: null, average_seconds: null, p95_seconds: null,
    succeeded: 0, failed: 0, cancelled: 0, request_bytes: 0, response_bytes: 0,
    preparation_retries: 0, reply_recoveries: 0 },
  live: { active: 0, queued: 0 }, capacity: 2, generated_at: now / 1000,
  recording_since: now / 1000, storage: { retention_days: 90 },
  daily: [], phase_averages: {}, recent: [], period: "today",
};
const response = (status) => ({ status, ok: status === 200, json: async () => report });
const settle = () => new Promise(setImmediate);

class Element {
  constructor() {
    this.listeners = {}; this.hidden = false; this.value = "";
    this.checked = true; this.textContent = "";
  }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  append() {}
  replaceChildren() {}
  setAttribute() {}
  focus() {}
}

function page({ saved = new Map(), time = now, blocked = false, fetcher } = {}) {
  const elements = new Map();
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  get("status-filter").value = "all";
  get("dashboard").hidden = get("login").hidden = true;
  const calls = [], events = {};
  let interval;
  const clock = { time };
  const storage = {
    getItem(key) { if (blocked) throw Error("disabled"); return saved.get(key) ?? null; },
    setItem(key, value) { if (blocked) throw Error("disabled"); saved.set(key, value); },
    removeItem(key) { if (blocked) throw Error("disabled"); saved.delete(key); },
  };
  vm.runInNewContext(source, {
    document: { getElementById: get, querySelectorAll: () => [], hidden: false,
      createElement: () => new Element(), createElementNS: () => new Element(),
      createTextNode: (text) => text },
    window: { addEventListener: (name, handler) => { events[name] = handler; } },
    localStorage: storage, AbortController, Intl,
    Date: class extends Date { static now() { return clock.time; } },
    setTimeout: () => 1, clearTimeout() {}, setInterval: (fn) => { interval = fn; },
    fetch: async (url, options) => {
      calls.push({ url, ...options });
      return fetcher ? fetcher(options) : response(options.headers.Authorization === "Bearer test-only" ? 200 : 401);
    },
  });
  return { get, calls, saved, clock, events, tick: () => interval(),
    click: (id) => get(id).listeners.click(),
    async login(remember = true) {
      get("api-key").value = "test-only";
      get("remember-key").checked = remember;
      get("login-form").listeners.submit({ preventDefault() {} });
      await settle();
    },
  };
}
const remembered = (expiresAt = now + sevenDays) => new Map([
  [storageKey, JSON.stringify({ key: "test-only", expiresAt })],
]);

test("verified login survives reload and refresh never extends the seven-day expiry", async () => {
  const first = page(); await settle(); await first.login();
  assert.equal(first.get("dashboard").hidden, false);
  const stored = first.saved.get(storageKey);
  assert.equal(JSON.parse(stored).expiresAt, now + sevenDays);
  const reload = page({ saved: first.saved, time: now + 86400000 }); await settle();
  assert.equal(reload.get("dashboard").hidden, false);
  reload.click("refresh"); await settle();
  assert.equal(first.saved.get(storageKey), stored);
  assert.ok(reload.calls.every((call) => call.headers.Authorization === "Bearer test-only"));
  assert.ok(reload.calls.every((call) => !call.url.includes("test-only")));
});

test("opting out retains only the current page login", async () => {
  const app = page(); await settle(); await app.login(false);
  assert.equal(app.get("dashboard").hidden, false);
  assert.equal(app.saved.size, 0);
  const reload = page({ saved: app.saved }); await settle();
  assert.equal(reload.get("login").hidden, false);
});

test("expired and corrupt entries are discarded before sending credentials", async () => {
  for (const raw of [JSON.stringify({ key: "test-only", expiresAt: now }), "{", "null",
    JSON.stringify({ key: 42, expiresAt: now + 1000 })]) {
    const saved = new Map([[storageKey, raw]]);
    const app = page({ saved }); await settle();
    assert.equal(app.calls[0].headers.Authorization, undefined);
    assert.equal(saved.size, 0);
  }
});

test("an open page expires even when automatic data refresh is disabled", async () => {
  const app = page({ saved: remembered() }); await settle();
  app.get("auto-refresh").checked = false;
  app.clock.time += sevenDays;
  app.tick();
  assert.equal(app.get("login").hidden, false);
  assert.equal(app.saved.size, 0);
  assert.equal(app.calls.length, 1);
});

test("401 clears a revoked saved key while temporary failures preserve it", async () => {
  const rejected = page({ saved: remembered(), fetcher: () => response(401) }); await settle();
  assert.equal(rejected.saved.size, 0);
  assert.equal(rejected.get("login").hidden, false);
  for (const fetcher of [() => response(503), () => { throw Error("offline"); }]) {
    const app = page({ saved: remembered(), fetcher }); await settle();
    assert.equal(app.saved.size, 1);
    assert.equal(app.get("retry-load").hidden, false);
  }
});

test("unverified login is never persisted and blocked storage permits memory login", async () => {
  const failed = page({ fetcher: () => response(503) }); await settle(); await failed.login();
  assert.equal(failed.saved.size, 0);
  const blocked = page({ blocked: true }); await settle(); await blocked.login();
  assert.equal(blocked.get("dashboard").hidden, false);
  assert.match(blocked.get("notice").textContent, /当前页面/);
  blocked.click("logout");
  assert.equal(blocked.get("login").hidden, false);
});

test("logout aborts an in-flight response and cannot re-save its key", async () => {
  let finish;
  const app = page({ fetcher: () => new Promise((resolve) => { finish = resolve; }) });
  await app.login();
  app.click("logout");
  assert.equal(app.calls.at(-1).signal.aborted, true);
  finish(response(200)); await settle();
  assert.equal(app.saved.size, 0);
  assert.equal(app.get("login").hidden, false);
});

test("another tab changing saved login invalidates this tab without erasing the replacement", async () => {
  const saved = remembered();
  const app = page({ saved }); await settle();
  const replacement = JSON.stringify({ key: "replacement-test-only", expiresAt: now + sevenDays });
  saved.set(storageKey, replacement);
  app.events.storage({ key: storageKey, newValue: replacement });
  assert.equal(app.get("login").hidden, false);
  assert.equal(saved.get(storageKey), replacement);
  app.tick();
  assert.equal(app.calls.length, 1);
});
