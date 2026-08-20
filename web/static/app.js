const $ = (id) => document.getElementById(id);
document.body.classList.add("js");

const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const fmtUSDC = (x) =>
  (x ?? 0).toLocaleString("ru-RU", { maximumFractionDigits: 2, minimumFractionDigits: 0 });

/* ---------- reveal-on-scroll ---------- */
const io = new IntersectionObserver(
  (entries) => {
    for (const e of entries) {
      if (e.isIntersecting) {
        e.target.classList.add("is-visible");
        io.unobserve(e.target);
      }
    }
  },
  { threshold: 0.1 }
);
document.querySelectorAll(".reveal").forEach((el) => io.observe(el));

/* ---------- count-up for numbers ---------- */
function animateCount(el, target) {
  if (REDUCED_MOTION) {
    el.textContent = fmtUSDC(target);
    return;
  }
  const dur = 900;
  const start = performance.now();
  function frame(now) {
    const p = Math.min((now - start) / dur, 1);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = fmtUSDC(target * eased);
    if (p < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

function setCount(el, value) {
  if (!el) return;
  el.classList.remove("skeleton");
  animateCount(el, value);
}

async function loadInfo() {
  try {
    const r = await fetch("/api/info");
    const info = await r.json();
    const username = info.bot_username;
    const tgLink = username ? "https://t.me/" + encodeURIComponent(username) : null;
    if (tgLink) {
      document.querySelectorAll("[data-tg-link]").forEach((a) => {
        a.href = tgLink;
        a.target = "_blank";
        a.rel = "noopener";
      });
    }
  } catch (e) { /* keep original links */ }
}

async function loadStats() {
  try {
    const r = await fetch("/api/stats");
    const s = await r.json();
    setCount($("stat-volume"), s.volume_usdc);
    setCount($("stat-vol30"), s.volume_30d_usdc);
    setCount($("stat-users"), s.users);
    setCount($("stat-markets"), s.open_markets);
    setCount($("stat-tx"), s.transactions);
    setCount($("stat-fees"), s.fees_usdc);
  } catch (e) { /* keep placeholders */ }
}

function fmtDay(day) {
  const [y, m, d] = day.split("-");
  return d + "." + m;
}

async function loadVolumeChart() {
  try {
    const r = await fetch("/api/volume_history?days=14");
    const days = await r.json();
    const el = $("volume-chart");
    if (!days.length) {
      el.innerHTML = '<div class="empty">Пока нет данных — объём появится после первых операций</div>';
      return;
    }
    const max = Math.max(...days.map((d) => d.volume_usdc), 1);
    el.innerHTML = days.map((d, i) => `
      <div class="chart-col" title="${fmtDay(d.day)}: ${fmtUSDC(d.volume_usdc)} USDC">
        <div class="chart-bar" style="height:${Math.max(d.volume_usdc / max * 100, 2)}%;animation-delay:${Math.min(i * 60, 600)}ms"></div>
        <div class="chart-day">${fmtDay(d.day)}</div>
      </div>`).join("");
  } catch (e) {
    $("volume-chart").innerHTML = '<div class="empty">Не удалось загрузить график</div>';
  }
}

async function loadWallet() {
  try {
    const r = await fetch("/api/wallet");
    const w = await r.json();
    $("wallet-address").textContent = w.address;
    $("wallet-balance").textContent = w.balance_usdc === null || w.balance_usdc === undefined
      ? "RPC недоступен"
      : fmtUSDC(w.balance_usdc) + " USDC";
  } catch (e) { /* keep placeholders */ }

  try {
    const r = await fetch("/api/solvency");
    const s = await r.json();
    $("wallet-liabilities").textContent = fmtUSDC(s.liabilities_usdc) + " USDC";
    $("wallet-solvent").textContent =
      s.solvent === true ? "✅ покрыто"
      : s.solvent === false ? "⚠️ недостаточно"
      : "RPC недоступен";
    if (s.vault_address) {
      $("wallet-vault-row").style.display = "";
      $("wallet-vault-addr").style.display = "";
      $("wallet-vault").textContent =
        s.vault_balance_usdc === null || s.vault_balance_usdc === undefined
          ? "RPC недоступен"
          : fmtUSDC(s.vault_balance_usdc) + " USDC";
      $("wallet-vault-addr").textContent = "Vault: " + s.vault_address;
    }
  } catch (e) { /* keep placeholders */ }
}

function statusBadge(status) {
  if (status === "open") return '<span class="chip chip-open">● открыт</span>';
  if (status === "resolved") return '<span class="chip chip-resolved">закрыт</span>';
  return '<span class="chip chip-cancelled">отменён</span>';
}

function relDeadline(ts) {
  const left = ts * 1000 - Date.now();
  if (left <= 0) return "дедлайн прошёл";
  const s = Math.floor(left / 1000);
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return "осталось " + d + "д " + h + "ч";
  if (h) return "осталось " + h + "ч " + m + "м";
  return "осталось " + m + "м";
}

function marketCard(m, i) {
  const deadline = m.expired
    ? "🕳️ истёк — можно вернуть деньги"
    : m.close_at
      ? "⏰ <span data-close-at=\"" + m.close_at + "\">" + relDeadline(m.close_at) + "</span>"
      : "";
  const winnerIdx = m.status === "resolved" && m.winner !== null && m.winner !== undefined ? m.winner : null;
  const options = m.options.map((o, j) => {
    const isWinner = winnerIdx !== null && o.index === winnerIdx;
    return `
    <div class="option${isWinner ? " option-winner" : ""}">
      <div class="option-top">
        <span class="option-label">${isWinner ? "🏆 " : ""}${escapeHtml(o.label)}</span>
        <span class="option-val">${fmtUSDC(o.pool_usdc)} USDC · ${o.probability}% · ${o.backers}👤</span>
      </div>
      <div class="bar"><div class="bar-fill${isWinner ? " bar-fill-win" : ""}" style="width:${Math.max(o.probability, 2)}%;animation-delay:${Math.min(i * 70 + j * 130, 900)}ms"></div></div>
    </div>`;
  }).join("");

  return `
    <div class="market-card" style="animation-delay:${Math.min(i * 70, 420)}ms">
      <div class="market-head">
        <span class="market-question">#${m.id} ${escapeHtml(m.question)}</span>
        <span class="market-meta">${deadline} · ${statusBadge(m.status)}</span>
      </div>
      ${options}
      <div class="market-footer">
        <span class="pot">Пул: <b>${fmtUSDC(m.pot_usdc)} USDC</b> · ${m.total_backers}👤</span>
        <span class="pot">
          <a class="m-link" href="/m/${m.id}">Подробнее →</a>
          <span style="margin-left: 10px">@${escapeHtml(m.creator.username || ("id" + m.creator.id))}</span>
        </span>
      </div>
    </div>`;
}

async function loadMarkets() {
  try {
    const r = await fetch("/api/markets");
    const markets = await r.json();
    const el = $("markets-list");
    if (!markets.length) {
      el.innerHTML = '<div class="empty">Открытых рынков пока нет — создай первый в боте: /bet create</div>';
      return;
    }
    el.innerHTML = markets.map(marketCard).join("");
  } catch (e) {
    $("markets-list").innerHTML = '<div class="empty">Не удалось загрузить рынки</div>';
  }
}

async function loadClosedMarkets() {
  try {
    const r = await fetch("/api/markets?status=resolved");
    const markets = await r.json();
    const el = $("closed-markets-list");
    if (!markets.length) {
      el.innerHTML = '<div class="empty">Закрытых рынков пока нет</div>';
      return;
    }
    el.innerHTML = markets.map(marketCard).join("");
  } catch (e) {
    $("closed-markets-list").innerHTML = '<div class="empty">Не удалось загрузить</div>';
  }
}

async function loadLeaderboard() {
  try {
    const r = await fetch("/api/leaderboard");
    const rows = await r.json();
    const el = $("leaderboard");
    if (!rows.length) {
      el.innerHTML = '<div class="empty">Пока пусто</div>';
      return;
    }
    const medals = ["🥇", "🥈", "🥉"];
    el.innerHTML = rows.map((row, i) => `
      <div class="lb-row" style="animation-delay:${Math.min(i * 60, 400)}ms">
        <span class="lb-place">${medals[i] || (i + 1)}</span>
        <span class="lb-name">@${escapeHtml(row.username)}</span>
        <span class="lb-amt">${fmtUSDC(row.total_usdc)} USDC</span>
      </div>`).join("");
  } catch (e) {
    $("leaderboard").innerHTML = '<div class="empty">Не удалось загрузить</div>';
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

loadInfo();
loadStats();
loadWallet();
loadMarkets();
loadClosedMarkets();
loadLeaderboard();
loadVolumeChart();
setInterval(loadStats, 15000);
setInterval(loadWallet, 30000);
setInterval(tickCountdowns, 10000);

function tickCountdowns() {
  document.querySelectorAll("[data-close-at]").forEach((el) => {
    const ts = parseInt(el.dataset.closeAt, 10);
    el.textContent = relDeadline(ts);
    el.classList.toggle("countdown-urgent", ts * 1000 - Date.now() < 3600 * 1000);
  });
}