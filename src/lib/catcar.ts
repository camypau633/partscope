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
  const requestUrl = `${PROXY_BASE_URL}?${params.toString()}`;
  let response: Response;

  try {
    response = await fetch(requestUrl, {
      method: "GET",
      headers: { Accept: "application/json" },
    });
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    throw new Error(
      [
        "Не удалось связаться с Yandex Cloud Function.",
        "Этап: отправка запроса из браузера.",
        `Адрес прокси: ${PROXY_BASE_URL}`,
        `Причина: ${reason}`,
        "Проверь доступность функции и CORS.",
      ].join("\\n"),
    );
  }

  const responseText = await response.text();
  let payload: unknown = null;
  try {
    payload = responseText ? JSON.parse(responseText) : null;
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const raw = payload && typeof payload === "object"
      ? payload as Record<string, unknown>
      : {};
    const diagnostic = raw["diagnostic"] && typeof raw["diagnostic"] === "object"
      ? raw["diagnostic"] as Record<string, unknown>
      : {};
    const details = [
      `HTTP прокси: ${response.status} ${response.statusText}`,
      typeof raw["error"] === "string" ? `Ошибка: ${raw["error"]}` : "",
      diagnostic["stage"] ? `Этап: ${String(diagnostic["stage"])}` : "",
      diagnostic["action"] ? `Действие: ${String(diagnostic["action"])}` : "",
      diagnostic["target"] ? `Сервер CatCar: ${String(diagnostic["target"])}` : "",
      diagnostic["error_code"] ? `Код ошибки: ${String(diagnostic["error_code"])}` : "",
      diagnostic["error_message"] ? `Подробности: ${String(diagnostic["error_message"])}` : "",
      diagnostic["upstream_status"] ? `HTTP CatCar: ${String(diagnostic["upstream_status"])} ${String(diagnostic["upstream_status_text"] ?? "")}` : "",
      diagnostic["redirect_location"] ? `Адрес перенаправления: ${String(diagnostic["redirect_location"])}` : "",
      diagnostic["elapsed_ms"] !== undefined ? `Время запроса: ${String(diagnostic["elapsed_ms"])} мс` : "",
      !payload && responseText ? `Ответ шлюза: ${responseText.slice(0, 1200)}` : "",
      !responseText ? "Тело ответа пустое." : "",
    ].filter(Boolean);

    throw new Error(details.join("\\n"));
  }

  if (!payload || typeof payload !== "object") {
    throw new Error(
      [
        "Прокси вернул некорректный ответ.",
        `HTTP: ${response.status}`,
        responseText ? `Ответ: ${responseText.slice(0, 1200)}` : "Тело ответа пустое.",
      ].join("\\n"),
    );
  }

  const raw = payload as Record<string, unknown>;
  if (raw["success"] !== true) {
    const errorText = typeof raw["error"] === "string" ? raw["error"] : "CatCar не подтвердил запрос.";
    throw new Error(errorText);
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
