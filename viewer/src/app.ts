import type { ApiState } from "./api";
import type { Route, RouteName } from "./router";
import type { FeatureCollection } from "geojson";

/** A GeoJSON result handed from the Database tab to the Map tab. */
export interface QueryResultLayer {
  label: string;
  geojson: FeatureCollection;
}

/** Shared state and services every view receives. */
export interface AppContext {
  api: Promise<ApiState>;
  /** Set by the Database tab; the map draws it and clears it with the chip's close button. */
  queryResult: QueryResultLayer | null;
  toast: (message: string) => void;
}

export interface View {
  el: HTMLElement;
  show(route: Route): void;
  hide(): void;
}

export type ViewFactory = (ctx: AppContext, route: Route) => Promise<View>;
export type { RouteName };
