export type CatCarPart = {
  cleaned_oem: string;
  raw_oem: string;
  pos: string;
  name: string;
};

export type CatCarNavigationItem = {
  label: string;
  url: string;
};

export type CatCarBrowsePayload = {
  success: true;
  page_url: string;
  title: string;
  items: CatCarNavigationItem[];
  scheme_image: string;
  parts: CatCarPart[];
};

const PROXY_BASE_URL =
  "https://functions.yandexcloud.net/d4eu75ck7v8arcv15ud0";

function cleanOem(value: unknown): string {
  return String(value ?? "").replace(/[^A-Za-z0-9]/g, "").toUpperCase();
}

function validateCatCarUrl(value: string): string {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("Некорректная ссылка CatCar.");
  }

  const host = parsed.hostname.toLowerCase();
  if (
    parsed.protocol !== "https:" ||
    (host !== "catcar.info" && host !== "www.catcar.info") ||
    parsed.username ||
    parsed.password ||
    parsed.port
  ) {
    throw new Error("Поддерживаются только HTTPS-ссылки catcar.info.");
  }
  return parsed.toString();
}

async function requestCatalog(params: URLSearchParams): Promise<CatCarBrowsePayload> {
  const response = await fetch(`${PROXY_BASE_URL}?${params.toString()}`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    let detail = "";
    try {
      const errorBody = (await response.json()) as { error?: string };
      detail = errorBody.error ? `: ${errorBody.error}` : "";
    } catch {
      // Ignore non-JSON gateway responses.
    }
    throw new Error(`CatCar proxy вернул HTTP ${response.status}${detail}`);
  }

  const payload: unknown = await response.json();
  if (!payload || typeof payload !== "object") {
    throw new Error("Прокси вернул некорректный ответ.");
  }

  const raw = payload as Record<string, unknown>;
  if (raw["success"] !== true) {
    throw new Error(
      typeof raw["error"] === "string" ? raw["error"] : "CatCar не подтвердил запрос.",
    );
  }

  const items = Array.isArray(raw["items"])
    ? raw["items"]
        .map((value): CatCarNavigationItem | null => {
          if (!value || typeof value !== "object") return null;
          const item = value as Record<string, unknown>;
          const label = String(item["label"] ?? "").trim();
          const rawUrl = String(item["url"] ?? "").trim();
          if (!label || !rawUrl) return null;
          try {
            return { label, url: validateCatCarUrl(rawUrl) };
          } catch {
            return null;
          }
        })
        .filter((item): item is CatCarNavigationItem => item !== null)
    : [];

  const pageUrl = typeof raw["page_url"] === "string" ? raw["page_url"] : "";
  const rawImage = typeof raw["scheme_image"] === "string" ? raw["scheme_image"].trim() : "";
  let schemeImage = "";
  if (rawImage) {
    try {
      const imageUrl = new URL(rawImage, pageUrl || "https://www.catcar.info/");
      if (
        (imageUrl.protocol === "https:" || imageUrl.protocol === "http:") &&
        (imageUrl.hostname === "ci.catcar.info" || imageUrl.hostname.endsWith(".catcar.info"))
      ) {
        schemeImage = imageUrl.toString();
      }
    } catch {
      schemeImage = "";
    }
  }

  const parts = Array.isArray(raw["parts"])
    ? raw["parts"]
        .map((value): CatCarPart | null => {
          if (!value || typeof value !== "object") return null;
          const item = value as Record<string, unknown>;
          const rawOem = String(item["raw_oem"] ?? "").trim();
          const cleanedOem = cleanOem(item["cleaned_oem"]) || cleanOem(rawOem);
          if (!cleanedOem) return null;
          return {
            cleaned_oem: cleanedOem,
            raw_oem: rawOem || cleanedOem,
            pos: String(item["pos"] ?? "—").trim() || "—",
            name: String(item["name"] ?? "Деталь").trim() || "Деталь",
          };
        })
        .filter((item): item is CatCarPart => item !== null)
    : [];

  return {
    success: true,
    page_url: pageUrl,
    title: typeof raw["title"] === "string" ? raw["title"] : "",
    items,
    scheme_image: schemeImage,
    parts,
  };
}

export function fetchCatCarCatalogs(): Promise<CatCarBrowsePayload> {
  return requestCatalog(new URLSearchParams({ action: "home" }));
}

export function fetchCatCarPage(
  targetUrl: string,
): Promise<CatCarBrowsePayload> {
  const safeTargetUrl = validateCatCarUrl(targetUrl);
  return requestCatalog(new URLSearchParams({ action: "page", url: safeTargetUrl }));
}
