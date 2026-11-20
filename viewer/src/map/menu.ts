import { CHEVRON, esc } from "../domain";

export interface MenuItem {
  id: string;
  label: string;
  hint?: string;
  disabled?: boolean;
  disabledReason?: string;
}

/**
 * A listbox dropdown (button + popover) with the keyboard behaviour of a native select: arrows move, Enter or Space
 * picks, Escape closes, Home/End jump. The popover scales in from the trigger and closes without an exit animation.
 */
export class Menu {
  readonly root: HTMLElement;
  private readonly button: HTMLButtonElement;
  private readonly list: HTMLElement;
  private items: MenuItem[] = [];
  private value = "";
  private active = 0;

  constructor(
    private readonly label: string,
    private readonly onPick: (id: string) => void,
  ) {
    this.root = document.createElement("div");
    this.root.className = "menu";
    this.root.innerHTML = `<button type="button" class="menu-btn glass" aria-haspopup="listbox" aria-expanded="false"><span class="menu-k">${esc(label)}</span><span class="menu-v"></span>${CHEVRON}</button><div class="menu-pop glass" role="listbox" tabindex="-1" aria-label="${esc(label)}" hidden></div>`;
    this.button = this.root.querySelector(".menu-btn") as HTMLButtonElement;
    this.list = this.root.querySelector(".menu-pop") as HTMLElement;
    this.button.addEventListener("click", () => (this.list.hidden ? this.open() : this.close()));
    this.button.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        this.open();
      }
    });
    this.list.addEventListener("keydown", (e) => this.onKey(e));
    this.list.addEventListener("click", (e) => {
      const opt = (e.target as HTMLElement).closest<HTMLElement>("[role=option]");
      if (opt) this.pick(Number(opt.dataset["i"]));
    });
    document.addEventListener("pointerdown", (e) => {
      if (!this.list.hidden && !this.root.contains(e.target as Node)) this.close();
    });
  }

  setItems(items: MenuItem[], value: string): void {
    this.items = items;
    this.value = value;
    this.render();
  }

  setValue(value: string): void {
    this.value = value;
    this.render();
  }

  private render(): void {
    const cur = this.items.find((i) => i.id === this.value);
    (this.button.querySelector(".menu-v") as HTMLElement).textContent = cur?.label ?? "";
    this.list.innerHTML = this.items
      .map(
        (it, i) =>
          `<div role="option" id="${esc(this.label)}-${i}" data-i="${i}" aria-selected="${it.id === this.value}" ${it.disabled ? 'aria-disabled="true"' : ""}><span class="opt-main"><b>${esc(it.label)}</b><small>${esc(it.disabled && it.disabledReason ? it.disabledReason : (it.hint ?? ""))}</small></span><svg class="opt-check" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="m3.5 8.5 3 3 6-7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></div>`,
      )
      .join("");
  }

  private open(): void {
    this.active = Math.max(0, this.items.findIndex((i) => i.id === this.value));
    this.list.hidden = false;
    this.button.setAttribute("aria-expanded", "true");
    this.mark();
    this.list.focus({ preventScroll: true });
  }

  close(refocus = false): void {
    if (this.list.hidden) return;
    this.list.hidden = true;
    this.button.setAttribute("aria-expanded", "false");
    if (refocus) this.button.focus();
  }

  private mark(): void {
    this.list.querySelectorAll<HTMLElement>("[role=option]").forEach((el, i) => el.classList.toggle("active", i === this.active));
    this.list.setAttribute("aria-activedescendant", `${this.label}-${this.active}`);
    this.list.querySelector<HTMLElement>(".active")?.scrollIntoView({ block: "nearest" });
  }

  private pick(i: number): void {
    const it = this.items[i];
    if (!it || it.disabled) return;
    this.value = it.id;
    this.render();
    this.close(true);
    this.onPick(it.id);
  }

  private onKey(e: KeyboardEvent): void {
    const n = this.items.length;
    if (e.key === "ArrowDown") this.active = (this.active + 1) % n;
    else if (e.key === "ArrowUp") this.active = (this.active + n - 1) % n;
    else if (e.key === "Home") this.active = 0;
    else if (e.key === "End") this.active = n - 1;
    else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      this.pick(this.active);
      return;
    } else if (e.key === "Escape" || e.key === "Tab") {
      this.close(e.key === "Escape");
      return;
    } else return;
    e.preventDefault();
    this.mark();
  }
}
