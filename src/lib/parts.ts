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

export function sanitizeSku(input: string): string {
  return input.replace(/[^0-9a-zA-Zа-яА-ЯёЁ]/g, "").toUpperCase();
}

/**
 * Configure VITE_PARTS_DATABASE_RAW_BASE to the directory containing per-part
 * JSON files, e.g. https://raw.githubusercontent.com/OWNER/REPOSITORY/main/parts
 * A file for SKU ABC123 is then requested from .../parts/ABC123.json.
 *
 * Optional VITE_PARTS_DATABASE_RAW_FALLBACKS can contain comma-separated
 * additional raw directory URLs. No proxy server is required.
 */
const RAW_DATABASE_BASES = [
  import.meta.env.VITE_PARTS_DATABASE_RAW_BASE ?? "",
  ...(import.meta.env.VITE_PARTS_DATABASE_RAW_FALLBACKS ?? "").split(","),
]
  .map((base) => base.trim().replace(/\/+$/, ""))
  .filter(Boolean);

const TIMEOUT_MS = 8000;

type Raw = Record<string, any>;

function emptyRemoteRecord(sku: string): Raw {
  return {
    sku,
    name: "",
    brand: "",
    analogs: [],
    analogues: [],
    compatibility: [],
    applicability: [],
    diagram: { diagramUrl: "", imageUrl: "", specifications: [], hotspots: [] },
    diagramUrl: "",
    specifications: [],
    specs: [],
    hotspots: [],
  };
}

/**
 * Fetch a per-part JSON record directly from GitHub Raw. A missing repository
 * configuration or 404 returns an empty record so the UI can show no results.
 * Other failures try the next configured raw source before returning empty.
 */
export async function fetchWithProxyRotation<T>(sku: string, rawBases = RAW_DATABASE_BASES): Promise<T> {
  if (!rawBases.length) return emptyRemoteRecord(sku) as T;

  for (const base of rawBases) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);
    try {
      const url = `${base}/${encodeURIComponent(sku)}.json`;
      const response = await fetch(url, {
        signal: controller.signal,
        headers: { Accept: "application/json" },
      });

      if (response.status === 404) continue;
      if (!response.ok) continue;

      const json = await response.json();
      if (!json || typeof json !== "object" || Array.isArray(json)) continue;
      return json as T;
    } catch {
      // Try the next configured GitHub Raw database.
    } finally {
      clearTimeout(timeout);
    }
  }

  return emptyRemoteRecord(sku) as T;
}

const str = (value: unknown) => (value == null ? "" : String(value));
const arr = (value: unknown): Raw[] => (Array.isArray(value) ? (value as Raw[]) : []);
const slug = (value: string) => value.toLowerCase().replace(/[^a-z0-9а-я]+/gi, "-");

/** Map both the documented open JSON format and legacy field names to the UI contract. */
export function normalizeRemote(
  sku: string,
  raw: Raw,
): Omit<PartData, "diagram"> & { diagramUrl: string; hotspots: Hotspot[] } {
  const analogues: Analogue[] = arr(raw["analogs"] ?? raw["analogues"])
    .map((item) => ({
      brand: str(item["brand"] ?? item["manufacturer"]),
      sku: sanitizeSku(str(item["sku"] ?? item["number"] ?? item["articleNumber"])),
      name: str(item["name"] ?? item["description"]),
    }))
    .filter((item) => item.sku);

  const applicability: Vehicle[] = arr(raw["compatibility"] ?? raw["applicability"])
    .map((item) => {
      const make = str(item["brand"] ?? item["make"] ?? item["manufacturer"]);
      const model = str(item["model"]);
      const engine = str(item["engine"]);
      const years = str(item["years"] ?? item["year"] ?? [item["yearFrom"], item["yearTo"]].filter(Boolean).join("–"));
      return { id: str(item["id"]) || slug(`${make}-${model}-${engine}-${years}`), make, model, years, engine };
    })
    .filter((item) => item.make || item.model);

  const diagram = (raw["diagram"] && typeof raw["diagram"] === "object" ? raw["diagram"] : {}) as Raw;
  const rawSpecs = diagram["specifications"] ?? raw["specifications"] ?? raw["specs"];
  const specs: Spec[] = Array.isArray(rawSpecs)
    ? arr(rawSpecs)
        .map((item) => ({
          key: str(item["key"] ?? item["label"] ?? item["name"]),
          label: str(item["name"] ?? item["label"] ?? item["key"]),
          value: str(item["value"]),
          unit: item["unit"] ? str(item["unit"]) : undefined,
        }))
        .filter((item) => item.label && item.value !== "")
    : rawSpecs && typeof rawSpecs === "object"
      ? Object.entries(rawSpecs as Raw).map(([key, value]) => ({ key, label: key, value: str(value) }))
      : [];

  const diagramUrl = str(diagram["diagramUrl"] ?? diagram["imageUrl"] ?? raw["diagramUrl"]);
  const hotspots: Hotspot[] = arr(diagram["hotspots"] ?? raw["hotspots"])
    .map((item, index) => {
      const hotspotSpecs = arr(item["specs"])
        .map((spec) => ({
          key: str(spec["key"] ?? spec["label"] ?? spec["name"]),
          label: str(spec["name"] ?? spec["label"] ?? spec["key"]),
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
        oem: str(item["oem"] ?? item["sku"] ?? item["partNumber"]),
        details: str(item["details"]),
        ...(hotspotSpecs.length ? { specs: hotspotSpecs } : {}),
      };
    })
    .filter((item) => Number.isFinite(item.x) && Number.isFinite(item.y));

  return {
    sku,
    name: str(raw["name"] ?? raw["partName"]),
    brand: str(raw["brand"] ?? raw["manufacturer"]),
    analogues,
    applicability,
    specs,
    diagramUrl,
    hotspots,
    source: "network",
    fetchedAt: Date.now(),
  };
}

const cacheKey = (sku: string) => `part:v5:${sku}`;
const imageKey = (sku: string) => `img:v5:${sku}`;

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
    if (!url) {
      resolve({ width: 0, height: 0 });
      return;
    }
    const image = new Image();
    image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
    image.onerror = () => resolve({ width: 0, height: 0 });
    image.src = url;
  });
}

/** Return live catalogue data, or a valid empty state when the part is not present. */
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
  const size = await measure(diagramUrl);
  const data: PartData = {
    ...rest,
    diagram: { imageUrl: diagramUrl, ...size, hotspots },
  };

  const hasRealData = Boolean(
    data.name ||
    data.brand ||
    data.analogues.length ||
    data.applicability.length ||
    data.specs.length ||
    data.diagram.imageUrl ||
    data.diagram.hotspots.length
  );

  // Don't cache missing records; a newly added database file should become visible immediately.
  if (hasRealData) {
    await set(cacheKey(sku), data).catch(() => {});
    await cacheImage(sku, diagramUrl);
  }

  const imageUrl = await resolveImage(sku, diagramUrl);
  return { ...data, diagram: { ...data.diagram, imageUrl }, fromCache: false };
}
