import "./styles/unlock.css";
import type { ApiState, AuthState } from "./api";
import { ApiError, authStatus, friendlyError, login, logout } from "./api";

/**
 * The access code, in one place. The server gates a few costly features (analyst chat, SQL console, new
 * comparisons) and answers them with 401. `withUnlock` runs an action, and on a 401 asks for the code in a dialog and
 * retries the action once. The state (is there a code at all, is this browser unlocked) feeds the top-bar chip.
 */

let state: AuthState = { enabled: false, authenticated: true };
const listeners = new Set<(s: AuthState) => void>();

const set = (s: AuthState) => {
  state = s;
  for (const fn of listeners) fn(s);
};

export function onAuthChange(fn: (s: AuthState) => void): void {
  listeners.add(fn);
  fn(state);
}

/** A gated call came back 401: remember that this browser is locked, whatever the status said before. */
const markLocked = (): void => set({ enabled: true, authenticated: false });

const isLocked = (err: unknown): boolean => err instanceof ApiError && err.status === 401;

export async function initAuth(apiPromise: Promise<ApiState>): Promise<void> {
  const api = await apiPromise;
  if (api.kind !== "ready") return;
  try {
    set(await authStatus(api));
  } catch {
    // status unavailable: stay open; a gated call that gets a 401 will switch the state
  }
}

export async function lock(apiPromise: Promise<ApiState>): Promise<void> {
  const api = await apiPromise;
  if (api.kind !== "ready") return;
  try {
    set({ ...(await logout(api)), authenticated: false });
  } catch {
    // leave the state as it is; the cookie is still valid
  }
}

/** Run `action`; if the server wants the access code, ask for it and retry once. Cancelling rethrows the 401. */
export async function withUnlock<T>(apiPromise: Promise<ApiState>, action: () => Promise<T>): Promise<T> {
  try {
    return await action();
  } catch (err) {
    if (!isLocked(err)) throw err;
    markLocked();
    if (!(await requestUnlock(apiPromise))) throw err;
    return action();
  }
}

let open: Promise<boolean> | null = null;

/** Show the unlock dialog (one at a time). Resolves true once the code was accepted, false when dismissed. */
export function requestUnlock(apiPromise: Promise<ApiState>): Promise<boolean> {
  open ??= showDialog(apiPromise).finally(() => (open = null));
  return open;
}

function showDialog(apiPromise: Promise<ApiState>): Promise<boolean> {
  return new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.className = "unlock";
    dlg.setAttribute("aria-labelledby", "unlock-title");
    dlg.innerHTML = `<form class="unlock-form" novalidate>
      <h2 id="unlock-title">Enter the access code</h2>
      <p class="muted">This feature needs the access code (sent with the submission).</p>
      <label class="sr-only" for="unlock-code">Access code</label>
      <input id="unlock-code" class="unlock-input" type="password" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="200" enterkeyhint="go" placeholder="Access code" required />
      <p class="unlock-error" role="alert" hidden></p>
      <div class="unlock-actions"><button type="button" class="btn ghost" data-act="cancel">Not now</button><button type="submit" class="btn primary">Unlock</button></div>
    </form>`;
    const form = dlg.querySelector<HTMLFormElement>("form")!;
    const input = dlg.querySelector<HTMLInputElement>("input")!;
    const error = dlg.querySelector<HTMLElement>(".unlock-error")!;
    const submit = dlg.querySelector<HTMLButtonElement>("[type=submit]")!;
    let ok = false;

    dlg.addEventListener("close", () => {
      dlg.remove();
      resolve(ok);
    });
    // Pointer dismissals fade out quickly; Escape (keyboard) and the success path close at once.
    const dismiss = () => {
      if (dlg.classList.contains("closing")) return;
      if (matchMedia("(prefers-reduced-motion: reduce)").matches) return dlg.close();
      dlg.classList.add("closing");
      setTimeout(() => dlg.close(), 120);
    };
    dlg.addEventListener("click", (e) => {
      // Only a click on the dialog element itself is the backdrop. Pressing Enter fires a synthetic click on the submit
      // button at (0, 0), which a coordinate test would mistake for a click outside and cancel the unlock.
      if (e.target !== dlg) return;
      const r = dlg.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dismiss();
    });
    dlg.querySelector("[data-act=cancel]")!.addEventListener("click", dismiss);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const code = input.value;
      if (!code) return input.focus();
      const api = await apiPromise;
      if (api.kind !== "ready") return;
      submit.disabled = input.disabled = true;
      error.hidden = true;
      try {
        set(await login(api, code));
        ok = true;
        dlg.close();
      } catch (err) {
        error.textContent = err instanceof ApiError && err.status === 401 ? "That access code is not right." : friendlyError(err, "the login");
        error.hidden = false;
        submit.disabled = input.disabled = false;
        input.select();
      }
    });
    document.body.append(dlg);
    dlg.showModal();
    input.focus();
  });
}

/** The top-bar indicator: hidden when the server has no access code. */
export function mountAuthChip(chip: HTMLElement, apiPromise: Promise<ApiState>): void {
  const label = chip.querySelector<HTMLElement>(".auth-label")!;
  const button = chip.querySelector<HTMLButtonElement>(".auth-btn")!;
  onAuthChange((s) => {
    chip.hidden = !s.enabled;
    chip.dataset["state"] = s.authenticated ? "unlocked" : "locked";
    label.textContent = s.authenticated ? "Unlocked" : "Locked";
    button.textContent = s.authenticated ? "Lock" : "Unlock";
  });
  button.addEventListener("click", () => void (state.authenticated ? lock(apiPromise) : requestUnlock(apiPromise)));
}
