import { get, set } from "idb-keyval";

export type Analogue = { brand: string; sku: string; name: string };
export type Vehicle = { id: string; make: string; model: string; years: string; engine: string };
export type Spec = { key: string; label: string; value: string | number; unit?: string };
export type Hotspot = {
  id: string;
  x: number;
  y: number;
  radius: number;
  name: string;
  oem: string;
  details: string;
  specs?: Spec[];
};

export type PartData = {
  sku: string;
  name: string;
  brand: string;
  analogues: Analogue[];
  applicability: Vehicle[];
  diagram: { imageUrl: string; width: number; height: number; hotspots: Hotspot[] };
  specs: Spec[];
  source: "network";
  fetchedAt: number;
};

/** Normalize an entered part number without inventing any replacement values. */
export function sanitizeSku(input: string): string {
  return input.replace(/[^0-9a-zA-Zа-яА-ЯёЁ]/g, "").toUpperCase();
}

/**
 * Data-source adapter pool. Each endpoint must accept GET ?sku=... and return
 * the JSON contract consumed by normalizeRemote(). Replace or extend endpoints
 * here without coupling UI components to a particular catalogue provider.
 */
export const PROXY_POOL: string[] = [
  "https://partscope-proxy.onrender.com/api/part",
  "https://partscope-proxy.up.railway.app/api/part",
];

const TIMEOUT_MS = 8000;

export async function fetchWithProxyRotation<T>(sku: string, pool = PROXY_POOL): Promise<T> {
  let lastError: unknown;
  for (const base of pool) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
    try {
      const res = await fetch(`${base}?sku=${encodeURIComponent(sku)}`, { signal: ctrl.signal });
      if (!res.ok) throw new Error(`HTTP ${res.status} from data source`);
      return (await res.json()) as T;
    } catch (error) {
      lastError = error;
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastError ?? new Error("No data sources are configured");
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Raw = { [key: string]: any };
const str = (value: unknown) => (value == null ? "" : String(value));
const arr = (value: unknown): Raw[] => (Array.isArray(value) ? (value as Raw[]) : []);
const slug = (value: string) => value.toLowerCase().replace(/[^a-z0-9а-я]+/gi, "-");

/** Convert a live provider response into the UI contract. No demo values are generated. */
export function normalizeRemote(
  sku: string,
  raw: Raw,
): Omit<PartData, "diagram"> & { diagramUrl: string; hotspots: Hotspot[] } {
  const analogues: Analogue[] = arr(raw["analogues"])
    .map((item) => ({
      brand: str(item["brand"] ?? item["manufacturer"]),
      sku: sanitizeSku(str(item["sku"] ?? item["number"] ?? item["articleNumber"])),
      name: str(item["name"] ?? item["description"]),
    }))
    .filter((item) => item.sku);

  const applicability: Vehicle[] = arr(raw["applicability"])
    .map((item) => {
      const make = str(item["make"] ?? item["manufacturer"]);
      const model = str(item["model"]);
      const engine = str(item["engine"]);
      const years = str(item["years"] ?? [item["yearFrom"], item["yearTo"]].filter(Boolean).join("–"));
      return { id: str(item["id"]) || slug(`${make}-${model}-${engine}`), make, model, years, engine };
    })
    .filter((item) => item.make || item.model);

  const rawSpecs = raw["specifications"] ?? raw["specs"];
  const specs: Spec[] = Array.isArray(rawSpecs)
    ? arr(rawSpecs)
        .map((item) => ({
          key: str(item["key"] ?? item["label"] ?? item["name"]),
          label: str(item["label"] ?? item["name"] ?? item["key"]),
          value: str(item["value"]),
          unit: item["unit"] ? str(item["unit"]) : undefined,
        }))
        .filter((item) => item.label && item.value !== "")
    : rawSpecs && typeof rawSpecs === "object"
      ? Object.entries(rawSpecs as Raw).map(([key, value]) => ({ key, label: key, value: str(value) }))
      : [];

  const diagram = raw["diagram"] as Raw | undefined;
  const diagramUrl = str(raw["diagramUrl"] ?? diagram?.["imageUrl"]);
  const hotspots: Hotspot[] = arr(raw["hotspots"] ?? diagram?.["hotspots"])
    .map((item, index) => {
      const hotspotSpecs = arr(item["specs"])
        .map((spec) => ({
          key: str(spec["key"] ?? spec["label"] ?? spec["name"]),
          label: str(spec["label"] ?? spec["name"] ?? spec["key"]),
          value: str(spec["value"]),
          unit: spec["unit"] ? str(spec["unit"]) : undefined,
        }))
        .filter((spec) => spec.label && spec.value !== "");
      return {
        id: str(item["id"] ?? index + 1),
        x: Number(item["x"]),
        y: Number(item["y"]),
        radius: Number(item["radius"] ?? 28),
        name: str(item["name"]),
        oem: str(item["oem"]),
        details: str(item["details"]),
        ...(hotspotSpecs.length ? { specs: hotspotSpecs } : {}),
      };
    })
    .filter((item) => Number.isFinite(item.x) && Number.isFinite(item.y));

  if (!analogues.length && !applicability.length && !specs.length && !diagramUrl) {
    throw new Error("The data source returned no catalogue data for this part number.");
  }

  return {
    sku,
    name: str(raw["name"]),
    brand: str(raw["brand"]),
    analogues,
    applicability,
    specs,
    diagramUrl,
    hotspots,
    source: "network",
    fetchedAt: Date.now(),
  };
}

// Versioned keys deliberately bypass older cached demo/mock records.
const cacheKey = (sku: string) => `part:v4:${sku}`;
const imageKey = (sku: string) => `img:v4:${sku}`;

async function cacheImage(sku: string, url: string) {
  if (!url || url.startsWith("data:") || url.startsWith("/")) return;
  try {
    const response = await fetch(url);
    if (response.ok) await set(imageKey(sku), await response.blob());
  } catch {
    // Some image hosts block cross-origin caching; the live URL can still render.
  }
}

async function resolveImage(sku: string, url: string) {
  const blob = await get<Blob>(imageKey(sku)).catch(() => undefined);
  return blob ? URL.createObjectURL(blob) : url;
}

function measure(url: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve) => {
    const image = new Image();
    image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
    image.onerror = () => resolve({ width: 0, height: 0 });
    image.src = url;
  });
}

/** Return only cached or live provider data. Network failures remain visible to the UI. */
export async function getPart(rawSku: string): Promise<PartData & { fromCache: boolean }> {
  const sku = sanitizeSku(rawSku);
  if (!sku) throw new Error("Enter a part number first.");

  const cached = await get<PartData>(cacheKey(sku)).catch(() => undefined);
  if (cached) {
    const imageUrl = await resolveImage(sku, cached.diagram.imageUrl);
    return { ...cached, diagram: { ...cached.diagram, imageUrl }, fromCache: true };
  }

  const raw = await fetchWithProxyRotation<Raw>(sku);
  const { diagramUrl, hotspots, ...rest } = normalizeRemote(sku, raw);
  const size = diagramUrl ? await measure(diagramUrl) : { width: 0, height: 0 };
  const data: PartData = {
    ...rest,
    diagram: { imageUrl: diagramUrl, ...size, hotspots },
  };

  await set(cacheKey(sku), data).catch(() => {});
  await cacheImage(sku, diagramUrl);
  const imageUrl = await resolveImage(sku, diagramUrl);
  return { ...data, diagram: { ...data.diagram, imageUrl }, fromCache: false };
}
