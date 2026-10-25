import { get, set } from "idb-keyval";
import demoDiagram from "@/assets/diagram-oil-filter.jpg";

export type Analogue = { brand: string; sku: string; name: string; price?: number | undefined };
export type Vehicle = { id: string; make: string; model: string; years: string; engine: string };
export type Hotspot = { id: string; x: number; y: number; radius: number; name: string; oem: string; details: string };
/** One physical dimension, e.g. { key: "diameter", label: "Диаметр", value: 76, unit: "мм" } */
export type Spec = { key: string; label: string; value: string | number; unit?: string | undefined };
/**
 * JSON contract returned by every proxy for GET ?sku=XXX (live TecDoc-style donor):
 * {
 *   sku?, name?, brand?,
 *   analogues:      [{ brand, sku | number, name?, price? }],
 *   applicability:  [{ make, model, years?, engine? , id? }],
 *   specifications: [{ key?, label | name, value, unit? }]  // or { "Диаметр": "76 мм", ... }
 *   diagramUrl:     "https://…/assembly.jpg",
 *   hotspots?:      [{ id, x, y, radius?, name, oem, details? }]   // x/y in image pixels
 * }
 */
export type PartData = {
  sku: string;
  name: string;
  brand: string;
  analogues: Analogue[];
  applicability: Vehicle[];
  diagram: { imageUrl: string; width: number; height: number; hotspots: Hotspot[] };
  specs: Spec[];
  source: "network" | "demo";
  fetchedAt: number;
};

/** Strip spaces, dashes, dots and any non-alphanumeric chars; uppercase. */
export function sanitizeSku(input: string): string {
  return input.replace(/[^0-9a-zA-Zа-яА-ЯёЁ]/g, "").toUpperCase();
}

/**
 * Proxy pool, tried in order. The first entry is this app's own CORS proxy
 * (/api/public/part), which forwards to the TecDoc donor configured on the server.
 * Add external fallbacks (Render / Railway / Yandex Cloud) after it.
 */
export const PROXY_POOL: string[] = [
  "/api/public/part",
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
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as T;
    } catch (e) {
      lastError = e;
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastError ?? new Error("All proxies failed");
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Raw = { [k: string]: any } & Partial<Record<"analogues" | "applicability" | "specifications" | "specs" | "diagramUrl" | "diagram" | "hotspots" | "name" | "brand", unknown>>;
const str = (v: unknown) => (v == null ? "" : String(v));
const arr = (v: unknown): Raw[] => (Array.isArray(v) ? (v as Raw[]) : []);
const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9а-я]+/gi, "-");

/** Convert any donor response into PartData. Throws if it contains nothing useful. */
export function normalizeRemote(sku: string, raw: Raw): Omit<PartData, "diagram"> & { diagramUrl: string; hotspots: Hotspot[] } {
  const analogues = arr(raw['analogues']).map((a) => ({
    brand: str(a['brand'] ?? a['manufacturer']),
    sku: sanitizeSku(str(a['sku'] ?? a['number'] ?? a['articleNumber'])),
    name: str(a['name'] ?? a['description']),
    price: typeof a['price'] === "number" ? a['price'] : undefined,
  })).filter((a) => a['sku']);
  const applicability = arr(raw['applicability']).map((v) => {
    const make = str(v['make'] ?? v['manufacturer']), model = str(v['model']);
    const engine = str(v['engine']), years = str(v['years'] ?? [v['yearFrom'], v['yearTo']].filter(Boolean).join("–"));
    return { id: str(v['id']) || slug(`${make}-${model}-${engine}`), make, model, years, engine };
  }).filter((v) => v['make'] || v['model']);
  const rawSpecs = raw['specifications'] ?? raw['specs'];
  const specs: Spec[] = Array.isArray(rawSpecs)
    ? arr(rawSpecs).map((s) => ({ key: str(s['key'] ?? s['label'] ?? s['name']), label: str(s['label'] ?? s['name'] ?? s['key']), value: str(s['value']), unit: s['unit'] ? str(s['unit']) : undefined }))
    : rawSpecs && typeof rawSpecs === "object"
      ? Object.entries(rawSpecs as Raw).map(([k, v]) => ({ key: k, label: k, value: str(v) }))
      : [];
  const diagramUrl = str(raw['diagramUrl'] ?? (raw['diagram'] as Raw | undefined)?.['imageUrl']);
  const hotspots = arr(raw['hotspots'] ?? (raw['diagram'] as Raw | undefined)?.['hotspots']).map((h, i) => ({
    id: str(h['id'] ?? i + 1), x: Number(h['x']), y: Number(h['y']), radius: Number(h['radius'] ?? 28),
    name: str(h['name']), oem: str(h['oem']), details: str(h['details']),
  })).filter((h) => Number.isFinite(h['x']) && Number.isFinite(h['y']));
  if (!analogues.length && !applicability.length && !specs.length && !diagramUrl) throw new Error("Empty donor response");
  return {
    sku, name: str(raw['name']), brand: str(raw['brand']),
    analogues, applicability, specs, diagramUrl, hotspots,
    source: "network", fetchedAt: Date.now(),
  };
}

const cacheKey = (sku: string) => `part:v3:${sku}`;
const imgKey = (sku: string) => `img:v3:${sku}`;

/** Download the blueprint once and keep the bytes in IndexedDB for offline use. */
async function cacheImage(sku: string, url: string) {
  if (!url || url.startsWith("data:") || url.startsWith("/")) return;
  try {
    const res = await fetch(url);
    if (res.ok) await set(imgKey(sku), await res.blob());
  } catch { /* CORS-blocked images still render via URL while online */ }
}
async function resolveImage(sku: string, url: string) {
  const blob = await get<Blob>(imgKey(sku)).catch(() => undefined);
  return blob ? URL.createObjectURL(blob) : url;
}
function measure(url: string): Promise<{ width: number; height: number }> {
  return new Promise((res) => {
    const img = new Image();
    img.onload = () => res({ width: img.naturalWidth || 1280, height: img.naturalHeight || 960 });
    img.onerror = () => res({ width: 1280, height: 960 });
    img.src = url;
  });
}

export async function getPart(rawSku: string): Promise<PartData & { fromCache: boolean }> {
  const sku = sanitizeSku(rawSku);
  const cached = await get<PartData>(cacheKey(sku)).catch(() => undefined);
  if (cached) {
    const imageUrl = await resolveImage(sku, cached.diagram.imageUrl);
    return { ...cached, diagram: { ...cached.diagram, imageUrl }, fromCache: true };
  }

  let data: PartData;
  try {
    const raw = await fetchWithProxyRotation<Raw>(sku);
    const { diagramUrl, hotspots, ...rest } = normalizeRemote(sku, raw);
    const size = diagramUrl ? await measure(diagramUrl) : { width: 0, height: 0 };
    data = { ...rest, diagram: { imageUrl: diagramUrl, ...size, hotspots } };
    await cacheImage(sku, diagramUrl);
  } catch {
    data = buildDemoPart(sku);
  }
  await set(cacheKey(sku), data).catch(() => {});
  const imageUrl = await resolveImage(sku, data.diagram.imageUrl);
  return { ...data, diagram: { ...data.diagram, imageUrl }, fromCache: false };
}

// ---------- Demo data (used when no proxy responds) ----------
function hash(s: string) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}
function rng(seed: number) {
  let t = seed;
  return () => {
    t = (t + 0x6d2b79f5) | 0;
    let r = Math.imul(t ^ (t >>> 15), 1 | t);
    r ^= r + Math.imul(r ^ (r >>> 7), 61 | r);
    return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
  };
}
const BRANDS = ["BOSCH", "DENSO", "NGK", "MANN-FILTER", "MAHLE", "VALEO", "KYB", "SACHS", "TRW", "FEBI", "HELLA", "BREMBO"];
const CARS: [string, string, string[]][] = [
  ["Toyota", "Camry XV70", ["2.5 A25A-FKS", "3.5 2GR-FKS"]],
  ["Toyota", "RAV4 XA50", ["2.0 M20A-FKS", "2.5 A25A-FXS"]],
  ["Lexus", "ES 250", ["2.5 A25A-FKS"]],
  ["Kia", "Sportage IV", ["2.0 G4NA", "1.6 G4FJ"]],
  ["Hyundai", "Tucson NX4", ["2.0 G4NL", "2.5 G4KN"]],
  ["Volkswagen", "Tiguan II", ["1.4 CZDA", "2.0 CZPA"]],
  ["Skoda", "Octavia A8", ["1.4 CZDA", "1.6 CWVA"]],
  ["Nissan", "Qashqai J11", ["2.0 MR20DD"]],
];
const PART_NAMES = ["Фильтр масляный", "Свеча зажигания", "Колодки тормозные", "Амортизатор", "Датчик кислорода", "Фильтр воздушный"];

function buildDemoPart(sku: string): PartData {
  const r = rng(hash(sku));
  const pick = <T,>(a: T[]): T => a[Math.floor(r() * a.length)]!;
  const name = PART_NAMES[0]!; // demo blueprint is an oil filter assembly
  const brand = pick(BRANDS);
  const analogues: Analogue[] = Array.from({ length: 5 + Math.floor(r() * 5) }, () => ({
    brand: pick(BRANDS),
    sku: Math.floor(r() * 1e9).toString(36).toUpperCase().padStart(7, "0"),
    name,
    price: Math.round(400 + r() * 4000),
  }));
  const applicability: Vehicle[] = [];
  CARS.filter(() => r() > 0.35).forEach(([make, model, engines]) =>
    engines.forEach((engine) => {
      const y = 2014 + Math.floor(r() * 6);
      applicability.push({ id: `${make}-${model}-${engine}`.replace(/\W+/g, "-").toLowerCase(), make, model, years: `${y}–${y + 5}`, engine });
    }),
  );
  if (!applicability.length) applicability.push({ id: "toyota-camry", make: "Toyota", model: "Camry XV70", years: "2017–2023", engine: "2.5 A25A-FKS" });

  const hotspots: Hotspot[] = [
    { id: "1", x: 620, y: 130, radius: 34, name: "Корпус масляного фильтра", oem: `15609${sku.slice(-4)}`, details: "Литой алюминий, 3 точки крепления к блоку." },
    { id: "2", x: 640, y: 330, radius: 30, name: "Болт крепления корпуса", oem: `90105${sku.slice(-5)}`, details: "M10×1.25, момент затяжки 39 Н·м." },
    { id: "3", x: 560, y: 462, radius: 30, name: "Кольцо уплотнительное", oem: `90301${sku.slice(-5)}`, details: "NBR, заменять при каждой замене фильтра." },
    { id: "4", x: 640, y: 650, radius: 40, name, oem: sku, details: `Искомая деталь. Производитель: ${brand}.` },
    { id: "5", x: 640, y: 818, radius: 26, name: "Шайба пробки", oem: `90430${sku.slice(-5)}`, details: "Медь, одноразовая." },
    { id: "6", x: 640, y: 895, radius: 30, name: "Пробка сливная", oem: `90341${sku.slice(-5)}`, details: "M12×1.25, момент затяжки 30 Н·м." },
  ];
  const d = 66 + Math.round(r() * 30);
  const specs: Spec[] = [
    { key: "diameter", label: "Диаметр", value: d, unit: "мм" },
    { key: "height", label: "Высота", value: 65 + Math.round(r() * 60), unit: "мм" },
    { key: "thread", label: "Размер резьбы", value: pick(["M20×1.5", "3/4\"-16 UNF", "M22×1.5", "M18×1.5"]) },
    { key: "thickness", label: "Толщина стенки", value: (0.4 + r() * 0.4).toFixed(2), unit: "мм" },
    { key: "seal_outer", label: "Наружный диаметр уплотнения", value: d - 4, unit: "мм" },
    { key: "seal_inner", label: "Внутренний диаметр уплотнения", value: d - 14, unit: "мм" },
    { key: "bypass", label: "Давление открытия перепускного клапана", value: (0.8 + r() * 1.2).toFixed(1), unit: "бар" },
    { key: "weight", label: "Масса", value: Math.round(200 + r() * 300), unit: "г" },
  ];
  return {
    sku, name, brand, analogues, applicability,
    diagram: { imageUrl: demoDiagram, width: 1280, height: 960, hotspots },
    specs,
    source: "demo", fetchedAt: Date.now(),
  };
}
