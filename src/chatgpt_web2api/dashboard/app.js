"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const authStorageKey = "web2api.stats.auth.v1";
  const rememberDuration = 7 * 24 * 60 * 60 * 1000;
  const phases = {
    validation: "检查请求",
    queue: "排队等待",
    prepare: "准备会话",
    model_selection: "切换模型",
    navigation: "打开会话",
    input: "输入问题",
    upload: "上传图片",
    send_ready: "等待发送",
    send: "确认提交",
    reply: "等待回复",
  };
  const statuses = { succeeded: "成功", failed: "失败", cancelled: "取消" };
  const state = {
    key: "",
    expiresAt: 0,
    rememberPending: false,
    authWarning: "",
    period: "today",
    connected: false,
    data: null,
    controller: null,
    version: 0,
  };
  const number = (value) => new Intl.NumberFormat("zh-CN").format(value);
  const seconds = (value) => (value == null ? "—" : `${value.toFixed(1)} s`);
  const bytes = (value) => {
    const units = ["B", "KB", "MB", "GB"];
    let unit = 0;
    while (value >= 1024 && unit < 3) {
      value /= 1024;
      unit++;
    }
    return `${value.toFixed(unit ? 1 : 0)} ${units[unit]}`;
  };
  const date = (value, full = false) =>
    new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      ...(full ? { month: "2-digit", day: "2-digit" } : {}),
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(new Date(value * 1000));
  function metric(id, value) {
    const [amount, ...unit] = value.split(" ");
    $(id).replaceChildren(document.createTextNode(amount));
    if (unit.length) {
      const small = document.createElement("small");
      small.textContent = unit.join(" ");
      $(id).append(small);
    }
  }
  function notice(message) {
    $("notice").textContent = message;
    $("notice").hidden = !message;
  }
  function connection(message, kind = "") {
    $("connection").textContent = message;
    $("connection").className = `connection ${kind}`;
  }
  function forgetSavedKey() {
    try {
      localStorage.removeItem(authStorageKey);
    } catch {
      // Browsers may disable storage; in-memory login must still work.
    }
  }
  function restoreSavedKey() {
    try {
      const saved = JSON.parse(localStorage.getItem(authStorageKey));
      if (
        saved &&
        typeof saved.key === "string" &&
        saved.key.trim() &&
        Number.isFinite(saved.expiresAt) &&
        saved.expiresAt > Date.now() &&
        saved.expiresAt <= Date.now() + rememberDuration
      ) {
        state.key = saved.key;
        state.expiresAt = saved.expiresAt;
        return;
      }
    } catch {
      // Ignore unavailable storage and malformed or outdated saved entries.
    }
    forgetSavedKey();
  }
  function rememberVerifiedKey() {
    if (!state.rememberPending || !state.key) return;
    state.rememberPending = false;
    const expiresAt = Date.now() + rememberDuration;
    try {
      localStorage.setItem(
        authStorageKey,
        JSON.stringify({ key: state.key, expiresAt }),
      );
      state.expiresAt = expiresAt;
    } catch {
      state.authWarning = "浏览器未允许保存登录，本次登录仅在当前页面有效。";
    }
  }
  function requireLogin(message = "", clearSaved = true) {
    if (clearSaved) forgetSavedKey();
    state.connected = false;
    state.data = null;
    state.key = "";
    state.expiresAt = 0;
    state.rememberPending = false;
    state.authWarning = "";
    $("dashboard").hidden = true;
    $("login").hidden = false;
    $("login-error").textContent = message;
    $("records-body").replaceChildren();
    connection("需要验证");
  }
  function endLogin(message = "", clearSaved = true) {
    ++state.version;
    state.controller?.abort();
    state.controller = null;
    requireLogin(message, clearSaved);
    notice("");
    $("loading").hidden = true;
    $("retry-load").hidden = true;
    $("refresh").disabled = false;
  }
  function loginExpired() {
    if (!state.expiresAt || state.expiresAt > Date.now()) return false;
    endLogin("记住登录已满 7 天，请重新输入服务 API Key。");
    return true;
  }
  function svgNode(name, attrs, text) {
    const element = document.createElementNS(
      "http://www.w3.org/2000/svg",
      name,
    );
    for (const [key, value] of Object.entries(attrs))
      element.setAttribute(key, value);
    if (text != null) element.textContent = text;
    return element;
  }
  function trend(rows) {
    const width = 680,
      height = 206,
      left = 32,
      right = 10,
      top = 14,
      bottom = 30,
      plotHeight = height - top - bottom;
    const svg = svgNode("svg", {
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-label": rows
        .map(
          (r) =>
            `${r.date}：成功 ${r.succeeded}，失败 ${r.failed}，取消 ${r.cancelled}`,
        )
        .join("；"),
    });
    const max = Math.max(4, ...rows.map((r) => r.total));
    const ceiling = Math.ceil(max / 4) * 4;
    for (let i = 0; i <= 4; i++) {
      const y = top + plotHeight * (1 - i / 4);
      svg.append(
        svgNode("line", {
          x1: left,
          y1: y,
          x2: width - right,
          y2: y,
          class: "gridline",
        }),
        svgNode(
          "text",
          { x: left - 9, y: y + 3, "text-anchor": "end" },
          number((ceiling * i) / 4),
        ),
      );
    }
    const slot = (width - left - right) / rows.length;
    const barWidth = Math.min(42, slot * 0.48);
    rows.forEach((row, index) => {
      const x = left + slot * (index + 0.5) - barWidth / 2;
      let y = top + plotHeight;
      for (const status of ["succeeded", "failed", "cancelled"]) {
        const h = (row[status] / ceiling) * plotHeight;
        if (h) {
          y -= h;
          const rect = svgNode("rect", {
            x,
            y,
            width: barWidth,
            height: h,
            rx: 1.5,
            class: `bar-${status}`,
          });
          rect.append(
            svgNode(
              "title",
              {},
              `${row.date} · ${statuses[status]} ${row[status]} 次`,
            ),
          );
          svg.append(rect);
        }
      }
      if (rows.length <= 7 || index % 5 === 0 || index === rows.length - 1)
        svg.append(
          svgNode(
            "text",
            { x: x + barWidth / 2, y: height - 7, "text-anchor": "middle" },
            row.date.slice(5).replace("-", "/"),
          ),
        );
    });
    $("trend").replaceChildren(svg);
    $("trend-empty").hidden = rows.some((r) => r.total);
    $("daily-summary").textContent =
      `共 ${rows.length} 天 · ${number(rows.reduce((n, r) => n + r.total, 0))} 次完成调用`;
  }
  function phaseChart(values) {
    const rows = Object.entries(values)
      .filter(([, v]) => v != null)
      .sort((a, b) => b[1] - a[1]);
    const max = Math.max(0.01, ...rows.map(([, v]) => v));
    $("phases").replaceChildren();
    $("phases-empty").hidden = !!rows.length;
    for (const [key, value] of rows) {
      const row = document.createElement("div");
      row.className = "phase-row";
      const label = document.createElement("span");
      label.textContent = phases[key] || key;
      const meter = document.createElement("meter");
      meter.min = 0;
      meter.max = max;
      meter.value = value;
      meter.setAttribute(
        "aria-label",
        `${label.textContent} ${seconds(value)}`,
      );
      const amount = document.createElement("span");
      amount.textContent = seconds(value);
      row.append(label, meter, amount);
      $("phases").append(row);
    }
  }
  function records() {
    const filter = $("status-filter").value;
    const all = state.data?.recent || [];
    const rows = all.filter((r) => filter === "all" || r.status === filter);
    $("record-count").textContent = all.length;
    $("records-body").replaceChildren();
    $("records-empty").hidden = !!rows.length;
    for (const row of rows) {
      const tr = document.createElement("tr");
      const lastPhase =
        row.phase === "reply" ? "回复处理" : phases[row.phase] || row.phase;
      const ending =
        row.status === "succeeded"
          ? "回复已完成"
          : `${lastPhase}时${statuses[row.status] || "结束"}`;
      const values = [
        date(row.started_at, true),
        row.request_id.slice(0, 10),
        row.status,
        row.stream ? "SSE" : "JSON",
        seconds(row.elapsed_seconds),
        `${bytes(row.request_bytes)} / ${bytes(row.response_bytes)}`,
        ending,
      ];
      values.forEach((value, index) => {
        const td = document.createElement("td");
        if (index === 2) {
          const badge = document.createElement("span");
          badge.className = `badge ${Object.hasOwn(statuses, value) ? value : "failed"}`;
          badge.textContent = statuses[value] || value;
          td.append(badge);
        } else {
          td.textContent = value;
          if (index === 1) {
            td.className = "mono";
            td.title = row.request_id;
          }
          if (index === 6) td.title = `结束阶段：${lastPhase}`;
        }
        tr.append(td);
      });
      $("records-body").append(tr);
    }
  }
  function render(data) {
    const s = data.summary;
    state.data = data;
    $("login").hidden = true;
    $("dashboard").hidden = false;
    metric("total", number(s.total));
    metric(
      "success-rate",
      s.success_rate == null ? "—" : `${(s.success_rate * 100).toFixed(1)} %`,
    );
    metric("average", seconds(s.average_seconds));
    metric("traffic", bytes(s.request_bytes + s.response_bytes));
    $("counts").textContent =
      `成功 ${number(s.succeeded)} · 失败 ${number(s.failed)} · 取消 ${number(s.cancelled)}`;
    $("p95").textContent = `P95 ${seconds(s.p95_seconds)}`;
    $("traffic-detail").textContent =
      `请求 ${bytes(s.request_bytes)} · 响应 ${bytes(s.response_bytes)}`;
    for (const key of ["active", "queued"]) $(key).textContent = data.live[key];
    $("capacity").textContent = data.capacity;
    $("updated").textContent = `更新于 ${date(data.generated_at)}`;
    $("recovery-summary").textContent =
      `准备阶段重试 ${s.preparation_retries} 次 · 回复读取恢复 ${s.reply_recoveries} 次`;
    $("retention").textContent =
      `记录保留 ${data.storage.retention_days} 天，不保存聊天正文或密钥。`;
    $("recording-since").textContent =
      `开始记录：${new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(data.recording_since * 1000))} · 不回填历史调用`;
    trend(data.daily);
    phaseChart(data.phase_averages);
    records();
    const warnings = [];
    if (state.authWarning) warnings.push(state.authWarning);
    if (data.demo)
      warnings.push("本地预览 · 以下为合成演示数据，未连接线上服务。");
    if (data.storage.write_errors || data.storage.dropped_records)
      warnings.push(
        `本次启动后有 ${data.storage.write_errors + data.storage.dropped_records} 条统计未保存，数据可能不完整。`,
      );
    if (data.storage.pending_records)
      warnings.push(
        `${data.storage.pending_records} 条记录正在保存，稍后自动更新。`,
      );
    notice(warnings.join(" "));
  }
  async function refresh() {
    if (loginExpired()) return;
    if (state.controller) state.controller.abort();
    const controller = new AbortController();
    state.controller = controller;
    const version = ++state.version;
    $("refresh").disabled = true;
    $("retry-load").hidden = true;
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(
        `/v1/stats?period=${encodeURIComponent(state.period)}`,
        {
          headers: state.key ? { Authorization: `Bearer ${state.key}` } : {},
          cache: "no-store",
          signal: controller.signal,
        },
      );
      if (version !== state.version) return;
      if (response.status === 401) {
        notice("");
        requireLogin(state.key ? "密钥无效，请检查后重试。" : "");
        return;
      }
      if (!response.ok)
        throw new Error(
          response.status === 503
            ? "统计存储暂时不可用，聊天服务不受影响。"
            : "暂时无法读取统计。",
        );
      const data = await response.json();
      if (version !== state.version) return;
      if (loginExpired()) return;
      rememberVerifiedKey();
      state.connected = true;
      render(data);
      connection("已连接", "connected");
    } catch (error) {
      if (version !== state.version) return;
      connection("更新失败", "error-state");
      notice(
        error.name === "AbortError"
          ? "读取统计超时。可点击刷新重试。"
          : error.message,
      );
      $("retry-load").hidden = !!state.data;
      if (state.data)
        $("updated").textContent =
          `数据停留于 ${date(state.data.generated_at)}`;
      if (state.data && state.data.period !== state.period) {
        const labels = { today: "今日", "7d": "近 7 天", "30d": "近 30 天" };
        notice(`${$("notice").textContent} 仍显示${labels[state.data.period]}的数据。`);
      }
    } finally {
      clearTimeout(timer);
      if (version === state.version) {
        state.controller = null;
        $("refresh").disabled = false;
        $("loading").hidden = true;
      }
    }
  }
  $("login-form").addEventListener("submit", (event) => {
    event.preventDefault();
    forgetSavedKey();
    state.key = $("api-key").value.trim();
    state.expiresAt = 0;
    state.rememberPending = $("remember-key")?.checked ?? true;
    state.authWarning = "";
    $("api-key").value = "";
    $("login-error").textContent = "";
    refresh();
  });
  $("logout").addEventListener("click", () => {
    endLogin();
    $("api-key").focus();
  });
  window.addEventListener("storage", (event) => {
    if (event.key !== authStorageKey && event.key !== null) return;
    if (!state.expiresAt) return;
    endLogin("登录保存状态已在其他页面更改，请重新登录。", false);
  });
  $("refresh").addEventListener("click", refresh);
  $("status-filter").addEventListener("change", records);
  $("retry-load").addEventListener("click", refresh);
  for (const button of document.querySelectorAll("[data-period]"))
    button.addEventListener("click", () => {
      state.period = button.dataset.period;
      for (const item of document.querySelectorAll("[data-period]")) {
        const selected = item === button;
        item.classList.toggle("selected", selected);
        item.setAttribute("aria-pressed", String(selected));
      }
      refresh();
    });
  setInterval(() => {
    if (loginExpired()) return;
    if (
      state.connected &&
      $("auto-refresh").checked &&
      !document.hidden &&
      !state.controller
    )
      refresh();
  }, 5000);
  restoreSavedKey();
  refresh();
})();
