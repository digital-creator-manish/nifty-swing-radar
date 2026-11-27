/**
 * Cloudflare Worker: Two-way Telegram -> GitHub Actions relay for Nifty Swing Radar.
 *
 * Required Worker Secrets (set in Cloudflare Dashboard or `wrangler secret put`):
 *   - TELEGRAM_BOT_TOKEN : Bot token from @BotFather
 *   - ALLOWED_CHAT_ID    : Your personal numeric Telegram chat ID
 *   - WEBHOOK_SECRET     : Random secret string set during Telegram `setWebhook`
 *   - GITHUB_TOKEN       : Fine-grained GitHub PAT (Actions: Read & write, Contents: Read)
 *
 * Non-secret vars (in wrangler.toml):
 *   - GITHUB_OWNER       : GitHub username (e.g. "digital-creator-manish")
 *   - GITHUB_REPO        : Repository name (e.g. "nifty-swing-radar")
 */

export default {
  async fetch(request, env) {
    if (request.method === "GET") {
      return new Response("Nifty Swing Radar Telegram relay is online.", { status: 200 });
    }
    if (request.method !== "POST") {
      return new Response("Method Not Allowed", { status: 405 });
    }

    // 1. Verify request came from Telegram using secret_token header
    const secretHeader = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (!env.WEBHOOK_SECRET || secretHeader !== env.WEBHOOK_SECRET) {
      return new Response("Unauthorized", { status: 401 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("Bad Request", { status: 400 });
    }

    const msg = update.message || update.edited_message;
    if (!msg || !msg.text || !msg.chat) {
      return new Response("OK", { status: 200 });
    }

    const chatId = String(msg.chat.id);
    // 2. Lock bot strictly to your personal Telegram Chat ID
    if (chatId !== String(env.ALLOWED_CHAT_ID).trim()) {
      await sendTelegram(env.TELEGRAM_BOT_TOKEN, chatId, "Unauthorized chat.");
      return new Response("OK", { status: 200 });
    }

    const parts = msg.text.trim().split(/\s+/);
    const cmd = parts[0].toLowerCase().split("@")[0];
    const arg = parts[1] || "";

    if (cmd === "/start" || cmd === "/help") {
      await sendTelegram(
        env.TELEGRAM_BOT_TOKEN,
        chatId,
        [
          "Nifty Swing Radar Commands:",
          "/scan - Run live Nifty 100 scan now",
          "/scan 500 - Scan with Rs. 500 max risk",
          "/status - Show latest logged trades from trades_log.csv",
          "/dryrun - Test workflow on GitHub (logs only)",
        ].join("\n")
      );
      return new Response("OK", { status: 200 });
    }

    if (cmd === "/scan" || cmd === "/dryrun") {
      const isDryRun = cmd === "/dryrun";
      let maxRisk = "";
      if (arg) {
        const parsed = Number(arg);
        if (!Number.isFinite(parsed) || parsed <= 0) {
          await sendTelegram(
            env.TELEGRAM_BOT_TOKEN,
            chatId,
            "Invalid risk amount. Example: /scan 500"
          );
          return new Response("OK", { status: 200 });
        }
        maxRisk = String(Math.round(parsed));
      }

      const ghRes = await triggerWorkflow(env, {
        dry_run: isDryRun ? "true" : "false",
        max_risk_inr: maxRisk,
      });

      if (!ghRes.ok) {
        await sendTelegram(
          env.TELEGRAM_BOT_TOKEN,
          chatId,
          `Failed to start GitHub Actions (${ghRes.status}): ${ghRes.error}`
        );
      } else if (isDryRun) {
        await sendTelegram(
          env.TELEGRAM_BOT_TOKEN,
          chatId,
          "Dry-run triggered on GitHub Actions. Check your repo's Actions tab in ~25s."
        );
      } else {
        const riskNote = maxRisk ? ` (Max Risk: Rs. ${maxRisk})` : "";
        await sendTelegram(
          env.TELEGRAM_BOT_TOKEN,
          chatId,
          `Scanning Nifty 100 now${riskNote}... Trade cards will arrive in ~20-30s.`
        );
      }
      return new Response("OK", { status: 200 });
    }

    if (cmd === "/status") {
      const summary = await fetchLatestTrades(env);
      await sendTelegram(env.TELEGRAM_BOT_TOKEN, chatId, summary);
      return new Response("OK", { status: 200 });
    }

    await sendTelegram(
      env.TELEGRAM_BOT_TOKEN,
      chatId,
      "Unknown command. Send /help to see available commands."
    );
    return new Response("OK", { status: 200 });
  },

  async scheduled(controller, env, ctx) {
    ctx.waitUntil(
      (async () => {
        const ghRes = await triggerWorkflow(env, {
          dry_run: "false",
          max_risk_inr: "",
        });
        if (!ghRes.ok && env.TELEGRAM_BOT_TOKEN && env.ALLOWED_CHAT_ID) {
          await sendTelegram(
            env.TELEGRAM_BOT_TOKEN,
            String(env.ALLOWED_CHAT_ID).trim(),
            `Hourly cron (${controller.cron}) failed to trigger GitHub Actions (${ghRes.status}): ${ghRes.error}`
          );
        }
      })()
    );
  },
};

async function triggerWorkflow(env, inputs) {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/swing-radar.yml/dispatches`;
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "nifty-swing-radar-cf-worker",
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main", inputs }),
  });

  if (resp.status === 204 || resp.ok) {
    return { ok: true, status: resp.status };
  }
  const text = await resp.text();
  return { ok: false, status: resp.status, error: text.slice(0, 200) };
}

async function fetchLatestTrades(env) {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/contents/trades_log.csv`;
  const resp = await fetch(url, {
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "nifty-swing-radar-cf-worker",
      "X-GitHub-Api-Version": "2022-11-28",
    },
  });
  if (!resp.ok) {
    return `Could not read trades_log.csv (HTTP ${resp.status}).`;
  }
  const data = await resp.json();
  const csvText = atob((data.content || "").replace(/\n/g, ""));
  const lines = csvText.trim().split(/\r?\n/);
  if (lines.length <= 1) {
    return "No trades logged in trades_log.csv yet.";
  }
  const recent = lines.slice(-6).slice(1); // Last 5 data rows
  const formatted = recent.map((row) => {
    const [dt, sym, entry, sl, tgt, qty, cap, vol, status] = row.split(",");
    return `• ${dt} | ${sym} x${qty} @ Rs.${entry} | SL ${sl} | TGT ${tgt} [${status}]`;
  });
  return `Latest Logged Signals:\n${formatted.join("\n")}`;
}

async function sendTelegram(token, chatId, text) {
  await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text }),
  });
}
