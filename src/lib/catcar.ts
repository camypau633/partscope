export type CatCarPart = {
  cleaned_oem: string;
  raw_oem: string;
  pos: string;
  name: string;
};

export type CatCarPayload = {
  success: true;
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
    parsed.password
  ) {
    throw new Error("Поддерживаются только HTTPS-ссылки catcar.info.");
  }

  return parsed.toString();
}

export async function fetchCatCarPage(
  targetUrl: string,
  signal?: AbortSignal,
): Promise<CatCarPayload> {
  const safeTargetUrl = validateCatCarUrl(targetUrl);
  const endpoint = `${PROXY_BASE_URL}?url=${encodeURIComponent(safeTargetUrl)}`;

  const response = await fetch(endpoint, {
    method: "GET",
    headers: { Accept: "application/json" },
    signal,
  });

  if (!response.ok) {
    let detail = "";
    try {
      const errorBody = (await response.json()) as { error?: string };
      detail = errorBody.error ? `: ${errorBody.error}` : "";
    } catch {
      // The function may return a non-JSON gateway error.
    }
    throw new Error(`CatCar proxy вернул HTTP ${response.status}${detail}`);
  }

  const payload: unknown = await response.json();
  if (!payload || typeof payload !== "object") {
    throw new Error("Прокси вернул некорректный ответ.");
  }

  const raw = payload as Record<string, unknown>;
  if (raw["success"] !== true || !Array.isArray(raw["parts"])) {
    throw new Error(
      typeof raw["error"] === "string"
        ? raw["error"]
        : "CatCar не вернул подтверждённые данные схемы.",
    );
  }

  const rawImage = typeof raw["scheme_image"] === "string"
    ? raw["scheme_image"].trim()
    : "";
  let schemeImage = "";

  if (rawImage) {
    try {
      const imageUrl = new URL(rawImage, safeTargetUrl);
      if (imageUrl.protocol === "https:" || imageUrl.protocol === "http:") {
        schemeImage = imageUrl.toString();
      }
    } catch {
      schemeImage = "";
    }
  }

  const parts = raw["parts"]
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
    .filter((item): item is CatCarPart => item !== null);

  return {
    success: true,
    scheme_image: schemeImage,
    parts,
  };
}
