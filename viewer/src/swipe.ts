/** Vertical before/after divider. Clips the compare map with CSS and reports nothing else. */
export class Swipe {
  private pct = 50;

  constructor(
    private readonly overlay: HTMLElement,
    private readonly handle: HTMLElement,
    private readonly clipTarget: HTMLElement,
  ) {
    handle.addEventListener("pointerdown", (e) => {
      handle.setPointerCapture(e.pointerId);
      this.moveTo(e.clientX);
    });
    handle.addEventListener("pointermove", (e) => {
      if (handle.hasPointerCapture(e.pointerId)) this.moveTo(e.clientX);
    });
    handle.addEventListener("keydown", (e) => {
      const step = e.shiftKey ? 10 : 2;
      if (e.key === "ArrowLeft") this.set(this.pct - step);
      else if (e.key === "ArrowRight") this.set(this.pct + step);
      else return;
      e.preventDefault();
    });
    this.set(this.pct);
  }

  setEnabled(on: boolean): void {
    this.overlay.hidden = !on;
    this.clipTarget.classList.toggle("is-clipped", on);
  }

  private moveTo(clientX: number): void {
    const r = this.overlay.getBoundingClientRect();
    this.set(((clientX - r.left) / r.width) * 100);
  }

  private set(pct: number): void {
    this.pct = Math.min(100, Math.max(0, pct));
    this.handle.style.left = `${this.pct}%`;
    this.handle.setAttribute("aria-valuenow", String(Math.round(this.pct)));
    this.clipTarget.style.setProperty("--swipe", `${this.pct}%`);
  }
}
