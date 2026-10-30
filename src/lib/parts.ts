import { get, set } from "idb-keyval";

export type Analogue = { partId?: string; brand: string; sku: string; name: string };
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
  partId: string;
  sku: string;
  name: string;
  brand: string;
  analogues: Analogue[];
  applicability: Vehicle[];
  diagram: { imageUrl: string; width: number; height: number; hotspots: Hotspot[] };
  specs: Spec[];
  technicalDrawings: string[];
  source: "network";
  fetchedAt: number;
};

const cleanPartNumber = (input: string): string =>
  input.toUpperCase().replace(/[^A-Z0-9]/g, "");

export function sanitizeSku(input: string): string {
  return cleanPartNumber(input);
}

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
    technical_drawings: [],
    technicalDrawings: [],
    hotspots: [],
  };
}

/**
 * Fetch one shard based on the first two cleaned SKU characters, then select
 * the exact record from its top-level SKU-keyed object.
 *
 * Example: SKU W712.9 -> clean SKU W7129 -> data/W7.json -> shard["W7129"].
 * No proxy rotation, third-party API, or fallback host is used.
 */
export async function fetchWithProxyRotation<T>(sku: string): Promise<T> {
  const cleanSku = cleanPartNumber(sku);
  if (!cleanSku) return emptyRemoteRecord("") as T;

  const prefix = cleanSku.slice(0, 2);
  const basePath = String(import.meta.env.BASE_URL || "/").replace(/\/?$/, "/");
  const url = `${basePath}data/${encodeURIComponent(prefix)}.json`;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);

  try {
    const response = await fetch(url, {
      method: "GET",
      signal: controller.signal,
      headers: { Accept: "application/json" },
      cache: "no-cache",
    });

    if (response.status === 404) {
      console.info(`[Part Finder] Shard not found for SKU "${cleanSku}" (${url}).`);
      return emptyRemoteRecord(cleanSku) as T;
    }
    if (!response.ok) {
      console.warn(`[Part Finder] Shard request failed: HTTP ${response.status} for ${url}`);
      return emptyRemoteRecord(cleanSku) as T;
    }

    const shard: unknown = await response.json();
    if (!shard || typeof shard !== "object" || Array.isArray(shard)) {
      console.error(`[Part Finder] Invalid shard object at ${url}`);
      return emptyRemoteRecord(cleanSku) as T;
    }

    const record = (shard as Raw)[cleanSku];
    if (!record || typeof record !== "object") {
      console.info(`[Part Finder] SKU "${cleanSku}" not present in shard ${url}.`);
      return emptyRemoteRecord(cleanSku) as T;
    }

    return record as T;
  } catch (error) {
    console.error(`[Part Finder] Could not fetch shard ${url}:`, error);
    return emptyRemoteRecord(cleanSku) as T;
  } finally {
    clearTimeout(timeout);
  }
}

const str = (value: unknown) => (value == null ? "" : String(value));
const arr = (value: unknown): Raw[] => (Array.isArray(value) ? (value as Raw[]) : []);
const slug = (value: string) => value.toLowerCase().replace(/[^a-z0-9а-я]+/gi, "-");

/** Map the generated JSON format and legacy field names to the UI contract. */
export function normalizeRemote(
  sku: string,
  raw: Raw,
): Omit<PartData, "diagram"> & { diagramUrl: string; hotspots: Hotspot[] } {
  const analogues: Analogue[] = arr(raw["analogs"] ?? raw["analogues"])
    .map((item) => ({
      partId: str(item["part_id"] ?? item["partId"]),
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

  const drawingValues = arr(raw["technical_drawings"] ?? raw["technicalDrawings"] ?? raw["drawings"]);
  const technicalDrawings = drawingValues
    .map((item) => typeof item === "string" ? item.trim() : str(item["url"] ?? item["imageUrl"] ?? item["diagramUrl"]))
    .filter((url) => (url.startsWith("http://") || url.startsWith("https://")));
  const singleDiagram = str(diagram["diagramUrl"] ?? diagram["imageUrl"] ?? raw["diagramUrl"]);
  if (singleDiagram && (singleDiagram.startsWith("http://") || singleDiagram.startsWith("https://")) && !technicalDrawings.includes(singleDiagram)) technicalDrawings.unshift(singleDiagram);
  const diagramUrl = singleDiagram || technicalDrawings[0] || "";
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
    partId: str(raw["part_id"] ?? raw["partId"]),
    sku: cleanPartNumber(sku),
    name: str(raw["name"] ?? raw["partName"]),
    brand: str(raw["brand"] ?? raw["manufacturer"]),
    analogues,
    applicability,
    specs,
    technicalDrawings,
    diagramUrl,
    hotspots,
    source: "network",
    fetchedAt: Date.now(),
  };
}

const cacheKey = (sku: string, partId: string) => `part:v9:${sku}:${partId || "legacy"}`;
const imageKey = (sku: string, partId: string) => `img:v9:${sku}:${partId || "legacy"}`;

async function cacheImage(sku: string, partId: string, url: string) {
  if (!url || url.startsWith("data:") || url.startsWith("/")) return;
  try {
    const response = await fetch(url);
    if (response.ok) await set(imageKey(sku, partId), await response.blob());
  } catch {
    // Some image hosts block cross-origin caching; the live URL can still render.
  }
}

async function resolveImage(sku: string, partId: string, url: string) {
  const blob = await get<Blob>(imageKey(sku, partId)).catch(() => undefined);
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

/** Return every TecDoc article record for a printed part number. */
export async function getParts(rawSku: string): Promise<Array<PartData & { fromCache: boolean }>> {
  const sku = sanitizeSku(rawSku);
  if (!sku) throw new Error("Enter a part number first.");
  const rawResult = await fetchWithProxyRotation<Raw | Raw[]>(sku);
  const records = Array.isArray(rawResult) ? rawResult : [rawResult];
  const output: Array<PartData & { fromCache: boolean }> = [];
  for (const raw of records) {
    if (!raw || typeof raw !== "object") continue;
    const partId = str(raw["part_id"] ?? raw["partId"]);
    const cached = await get<PartData>(cacheKey(sku, partId)).catch(() => undefined);
    if (cached) {
      const imageUrl = await resolveImage(sku, partId, cached.diagram.imageUrl);
      output.push({ ...cached, diagram: { ...cached.diagram, imageUrl }, fromCache: true });
      continue;
    }
    const { diagramUrl, hotspots, ...rest } = normalizeRemote(sku, raw);
    const size = await measure(diagramUrl);
    const data: PartData = { ...rest, diagram: { imageUrl: diagramUrl, ...size, hotspots } };
    const hasRealData = Boolean(data.name || data.brand || data.analogues.length ||
      data.applicability.length || data.specs.length || data.diagram.imageUrl || data.diagram.hotspots.length);
    if (hasRealData) {
      await set(cacheKey(sku, partId), data).catch(() => {});
      await cacheImage(sku, partId, diagramUrl);
    }
    const imageUrl = await resolveImage(sku, partId, diagramUrl);
    output.push({ ...data, diagram: { ...data.diagram, imageUrl }, fromCache: false });
  }
  return output;
}

/** Return one selected TecDoc article; legacy callers without an ID get the first candidate. */
export async function getPart(rawSku: string, partId?: string): Promise<PartData & { fromCache: boolean }> {
  const records = await getParts(rawSku);
  const selected = partId ? records.find((record) => record.partId === partId) : records[0];
  if (selected) return selected;
  const sku = sanitizeSku(rawSku);
  const { diagramUrl, hotspots, ...rest } = normalizeRemote(sku, emptyRemoteRecord(sku));
  return { ...rest, partId: "", diagram: { imageUrl: diagramUrl, width: 0, height: 0, hotspots }, fromCache: false };
}
