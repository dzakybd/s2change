import "../styles/chat.css";
import type { ApiState, ChatMessage } from "../api";
import { brief, chat, friendlyError } from "../api";
import { chartElement } from "../charts";
import { LIVE_URL } from "../domain";
import { esc, renderMarkdown } from "../markdown";
import { renderRich } from "../rich";
import type { AgentAction, AgentResponse, AgentTable } from "../types";

export const SUGGESTIONS = [
  "How much changed and why?",
  "Show the 5 largest changes in the mine footprint",
  "Which facility changed the most?",
  "Which changes are likely burn scars?",
];

/** What the chat can ask the viewer to do. Each returns an error message, or null on success. */
export interface AgentHost {
  flyTo(bbox: [number, number, number, number]): string | null;
  highlight(runId: string, ids: number[], fit: boolean): Promise<string | null>;
  showRun(runId: string): Promise<string | null>;
  /** Clear anything the agent drew (highlights). */
  reset(): void;
}

const SEND = `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M3.4 20.4 21 12 3.4 3.6l-.02 6.52L15 12 3.38 13.88l.02 6.52Z" fill="currentColor"/></svg>`;

function actionLabel(a: AgentAction): string {
  switch (a.type) {
    case "fly_to":
      return "Zoom to area";
    case "highlight":
      return `Highlight ${a.ids.length} polygon${a.ids.length === 1 ? "" : "s"}`;
    case "show_run":
      return `Show run ${a.run_id}`;
  }
}

function cell(v: string | number | boolean | null): { text: string; num: boolean } {
  if (typeof v === "number") return { text: v.toLocaleString("en-US", { maximumFractionDigits: 3 }), num: true };
  if (v === null || v === undefined) return { text: "—", num: false };
  return { text: String(v), num: false };
}

function tableElement(t: AgentTable): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "table-card";
  const head = t.columns.map((c) => `<th>${esc(String(c))}</th>`).join("");
  const body = t.rows
    .map((r) => `<tr>${t.columns.map((_c, i) => { const c = cell(r[i] ?? null); return `<td${c.num ? ' class="num"' : ""}>${esc(c.text)}</td>`; }).join("")}</tr>`)
    .join("");
  wrap.innerHTML = `${t.title ? `<div class="table-title">${esc(t.title)}</div>` : ""}<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  return wrap;
}

function traceElement(calls: AgentResponse["tool_calls"]): HTMLElement {
  const d = document.createElement("details");
  d.className = "trace";
  const items = calls
    .map((c) => {
      let args = "";
      try {
        args = JSON.stringify(c.args ?? {}, null, 2);
      } catch {
        args = String(c.args);
      }
      return `<li><code class="tool">${esc(c.name)}</code><pre>${esc(args)}</pre></li>`;
    })
    .join("");
  d.innerHTML = `<summary>How I got this <span>${calls.length} tool call${calls.length === 1 ? "" : "s"}</span></summary><ol>${items}</ol>`;
  return d;
}

export function mountAnalyst(root: HTMLElement, host: AgentHost, apiPromise: Promise<ApiState>): void {
  root.innerHTML = `
    <header class="chat-head">
      <div>
        <div class="eyebrow">Analyst agent</div>
        <h1>Ask about the changes</h1>
      </div>
      <div class="chat-tools">
        <button type="button" class="btn sm" id="chat-brief" disabled>Generate monitoring brief</button>
        <button type="button" class="btn ghost sm" id="chat-clear" hidden>New chat</button>
      </div>
    </header>
    <div id="chat-banner"></div>
    <div class="chat-scroll" id="chat-scroll" aria-live="polite">
      <div class="welcome" id="chat-welcome">
        <p>Ask about the change result. Answers can include charts, tables and map actions that move the map beside this panel, and every answer shows the tool calls behind it.</p>
        <div class="chips" id="chat-chips">${SUGGESTIONS.map((s) => `<button type="button" class="chip">${esc(s)}</button>`).join("")}</div>
      </div>
      <div id="chat-log"></div>
    </div>
    <form class="composer" id="chat-form" autocomplete="off">
      <textarea id="chat-input" rows="1" placeholder="Checking the analyst service…" aria-label="Ask the analyst agent" disabled></textarea>
      <button type="submit" class="send" id="chat-send" aria-label="Send" disabled>${SEND}</button>
    </form>`;

  const $ = <T extends HTMLElement>(sel: string): T => {
    const el = root.querySelector<T>(sel);
    if (!el) throw new Error(`missing ${sel}`);
    return el;
  };
  const log = $("#chat-log");
  const scroller = $("#chat-scroll");
  const input = $<HTMLTextAreaElement>("#chat-input");
  const send = $<HTMLButtonElement>("#chat-send");
  const form = $<HTMLFormElement>("#chat-form");
  const clear = $<HTMLButtonElement>("#chat-clear");
  const welcome = $("#chat-welcome");
  const chips = [...root.querySelectorAll<HTMLButtonElement>(".chip")];
  const briefBtn = $<HTMLButtonElement>("#chat-brief");
  let briefBusy = false;

  const history: ChatMessage[] = [];
  let api: ApiState | null = null;
  let busy = false;
  let controller: AbortController | null = null;

  const scrollDown = () => requestAnimationFrame(() => { scroller.scrollTop = scroller.scrollHeight; });
  const syncControls = () => {
    const on = api?.kind === "ready" && !busy;
    input.disabled = api?.kind !== "ready";
    send.disabled = !on || !input.value.trim();
    for (const c of chips) c.disabled = !on;
    briefBtn.disabled = api?.kind !== "ready" || briefBusy;
  };

  void apiPromise.then((state) => {
    api = state;
    const banner = $("#chat-banner");
    if (state.kind === "disabled") {
      const why = state.reason === "static-host" ? "This page is served statically (GitHub Pages), so there is no analyst backend behind it." : "The analyst API did not answer its health check.";
      banner.innerHTML = `<div class="callout offline"><b>Live agent runs at <a href="${LIVE_URL}" target="_blank" rel="noopener noreferrer">${LIVE_URL}</a></b><p>${why} The rest of the app keeps working from the static exports.</p></div>`;
      input.placeholder = "Chat is unavailable on this host";
    } else {
      input.placeholder = "Ask about the changes…";
      if (state.mock) banner.innerHTML = `<div class="callout mock"><b>Mock mode</b><p>Answers are canned examples, not the live agent.</p></div>`;
    }
    syncControls();
  });

  const addUser = (text: string) => {
    const el = document.createElement("div");
    el.className = "msg user";
    el.textContent = text;
    log.append(el);
  };

  const runAction = async (a: AgentAction, fit: boolean): Promise<string | null> => {
    try {
      switch (a.type) {
        case "fly_to":
          return host.flyTo(a.bbox);
        case "highlight":
          return await host.highlight(a.run_id, a.ids, fit);
        case "show_run":
          return await host.showRun(a.run_id);
      }
    } catch (err) {
      return err instanceof Error ? err.message : "Action failed";
    }
    return null;
  };

  const renderAnswer = (res: AgentResponse): HTMLElement => {
    const el = document.createElement("div");
    el.className = "msg assistant";
    const body = document.createElement("div");
    body.className = "md";
    body.innerHTML = renderMarkdown(res.answer || "_The analyst returned no text._");
    el.append(body);
    for (const c of res.charts) el.append(chartElement(c));
    for (const t of res.tables) el.append(tableElement(t));
    const actions = res.actions.filter((a) => ["fly_to", "highlight", "show_run"].includes(a.type));
    if (actions.length) {
      const row = document.createElement("div");
      row.className = "actions";
      const note = document.createElement("div");
      note.className = "action-note";
      const fit = !actions.some((a) => a.type === "fly_to");
      const buttons = actions.map((a) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "action";
        b.textContent = actionLabel(a);
        b.title = "Run this map action again";
        b.addEventListener("click", () => void exec(a, b));
        row.append(b);
        return [a, b] as const;
      });
      const exec = async (a: AgentAction, b: HTMLButtonElement) => {
        const err = await runAction(a, fit);
        b.classList.toggle("failed", err !== null);
        note.textContent = err ?? "";
      };
      el.append(row, note);
      // Run once on arrival, in order (fly first, then highlight, so the fit does not fight the zoom).
      void (async () => {
        for (const [a, b] of buttons) await exec(a, b);
      })();
    }
    if (res.tool_calls.length) el.append(traceElement(res.tool_calls));
    return el;
  };

  const ask = async (text: string) => {
    if (busy || api?.kind !== "ready") return;
    busy = true;
    welcome.hidden = true;
    clear.hidden = false;
    document.querySelector("#chat-log .msg.error")?.remove();
    addUser(text);
    history.push({ role: "user", content: text });
    const pending = document.createElement("div");
    pending.className = "msg assistant loading";
    pending.innerHTML = `<span class="dots" aria-hidden="true"><i></i><i></i><i></i></span><span>Analyzing the data…</span>`;
    log.append(pending);
    syncControls();
    scrollDown();
    controller = new AbortController();
    const timeout = window.setTimeout(() => controller?.abort(), 120_000);
    try {
      const res = await chat(api, history.slice(-20), controller.signal);
      history.push({ role: "assistant", content: res.answer });
      const answer = renderAnswer(res);
      pending.replaceWith(answer);
      // Long answers: keep their first lines in view instead of jumping to the end.
      if (answer.offsetHeight > scroller.clientHeight - 24) answer.scrollIntoView({ block: "start" });
      else scrollDown();
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError" && !busy) return;
      history.pop();
      const el = document.createElement("div");
      el.className = "msg error";
      el.innerHTML = `<p>${esc(err instanceof DOMException && err.name === "AbortError" ? "The analyst took too long to answer. Try a narrower question." : friendlyError(err, "the analyst"))}</p><button type="button" class="action retry">Try again</button>`;
      el.querySelector<HTMLButtonElement>(".retry")!.addEventListener("click", () => {
        el.previousElementSibling?.remove(); // the user message gets re-added by ask()
        el.remove();
        void ask(text);
      });
      pending.replaceWith(el);
      scrollDown();
    } finally {
      window.clearTimeout(timeout);
      busy = false;
      controller = null;
      syncControls();
    }
  };

  briefBtn.addEventListener("click", async () => {
    if (api?.kind !== "ready" || briefBusy) return;
    briefBusy = true;
    welcome.hidden = true;
    clear.hidden = false;
    syncControls();
    const card = document.createElement("div");
    card.className = "msg assistant brief loading";
    card.innerHTML = `<span class="dots" aria-hidden="true"><i></i><i></i><i></i></span><span>Building the monitoring brief from the database…</span>`;
    log.append(card);
    scrollDown();
    try {
      const doc = await renderRich(await brief(api), { figures: false });
      doc.className = "md brief-doc";
      card.className = "msg assistant brief";
      card.replaceChildren(doc);
      card.scrollIntoView({ block: "start" });
    } catch (err) {
      card.className = "msg error";
      card.innerHTML = `<p>${esc(friendlyError(err, "the brief service"))}</p>`;
    } finally {
      briefBusy = false;
      syncControls();
    }
  });

  const submit = () => {
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    autosize();
    void ask(text);
  };
  const autosize = () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 110)}px`;
    syncControls();
  };
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    submit();
  });
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      submit();
    }
  });
  for (const c of chips) c.addEventListener("click", () => void ask(c.textContent ?? ""));
  clear.addEventListener("click", () => {
    controller?.abort();
    busy = false;
    history.length = 0;
    host.reset();
    log.replaceChildren();
    welcome.hidden = false;
    clear.hidden = true;
    syncControls();
  });
}
