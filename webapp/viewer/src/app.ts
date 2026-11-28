import type { ApiState } from "./api";
import type { Route } from "./router";

/** Shared state and services every view receives. */
export interface AppContext {
  api: Promise<ApiState>;
}

export interface View {
  el: HTMLElement;
  show(route: Route): void;
}
