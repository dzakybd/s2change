/** Capture date of the Esri World Imagery tile under a point, from the service's identify endpoint (null if unknown). */
const IDENTIFY = "https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/identify";
const cache = new Map<string, string | null>();

export async function esriCaptureDate(lng: number, lat: number): Promise<string | null> {
  const key = `${lng.toFixed(3)},${lat.toFixed(3)}`;
  if (cache.has(key)) return cache.get(key) ?? null;
  const d = 0.01;
  const params = new URLSearchParams({
    geometry: `${lng},${lat}`,
    geometryType: "esriGeometryPoint",
    sr: "4326",
    layers: "top",
    tolerance: "1",
    mapExtent: `${lng - d},${lat - d},${lng + d},${lat + d}`,
    imageDisplay: "1000,1000,96",
    returnGeometry: "false",
    f: "json",
  });
  let date: string | null = null;
  try {
    const res = await fetch(`${IDENTIFY}?${params}`);
    const json = (await res.json()) as { results?: { attributes?: Record<string, string> }[] };
    const raw = json.results?.[0]?.attributes?.["DATE (YYYYMMDD)"];
    if (raw && /^\d{8}$/.test(raw)) date = `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6)}`;
  } catch {
    return null; // not cached: a network blip should not stick
  }
  cache.set(key, date);
  return date;
}
