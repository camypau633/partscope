import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { lazy, Suspense, useState } from "react";
import { Camera, CarFront, Loader2, Search, Wifi } from "lucide-react";
import { sanitizeSku } from "@/lib/parts";

const Scanner = lazy(() => import("@/components/Scanner").then((module) => ({ default: module.Scanner })));

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "PartScope — поиск автозапчастей и аналогов" },
      { name: "description", content: "Поиск автозапчастей по введённому OEM-номеру, аналоги, применимость и схемы." },
      { property: "og:title", content: "PartScope — поиск автозапчастей" },
      { property: "og:description", content: "Аналоги, применимость и схемы узлов по номеру детали." },
    ],
  }),
  component: Home,
});

function Home() {
  const [query, setQuery] = useState("");
  const [scanOpen, setScanOpen] = useState(false);
  const [directTest, setDirectTest] = useState<{ status: "success" | "http-error" | "network-error"; message: string; details: string } | null>(null);
  const [testingDirect, setTestingDirect] = useState(false);
  const navigate = useNavigate();
  const testDirectCatCarAccess = async () => {
    setTestingDirect(true);
    setDirectTest(null);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch("https://www.catcar.info/", {
        method: "GET",
        mode: "cors",
        credentials: "omit",
        signal: controller.signal,
        headers: { Accept: "text/html,application/xhtml+xml" },
      });
      const body = await response.text();
      setDirectTest({
        status: response.ok ? "success" : "http-error",
        message: response.ok
          ? "Прямое подключение работает: браузер прочитал ответ CatCar."
          : `CatCar ответил HTTP ${response.status} ${response.statusText}.`,
        details: `HTTP: ${response.status} ${response.statusText}\nРазмер ответа: ${body.length} символов\nНачало ответа:\n${body.slice(0, 400)}`,
      });
    } catch (error) {
      const reason = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
      setDirectTest({
        status: "network-error",
        message: "Браузер не смог прочитать ответ CatCar.",
        details: `${reason}\nВозможны CORS или сетевая ошибка; по этому результату их нельзя надёжно различить.`,
      });
    } finally {
      window.clearTimeout(timeout);
      setTestingDirect(false);
    }
  };
  const go = (value: string) => {
    const sku = sanitizeSku(value);
    if (sku) navigate({ to: "/search", search: { part: sku } });
  };

  return (
    <main className="grid-bg flex min-h-dvh flex-col">
      <div className="flex flex-1 flex-col items-center justify-center px-5">
        <img src={`${import.meta.env.BASE_URL}icon-192.png`} alt="" width={64} height={64} className="mb-5 rounded-2xl" />
        <h1 className="text-3xl font-bold tracking-tight">PartScope</h1>
        <p className="mb-8 mt-1 text-sm text-muted-foreground">Аналоги · Применимость · Схемы</p>
        <form onSubmit={(event) => { event.preventDefault(); go(query); }} className="w-full max-w-md">
          <div className="flex items-center gap-2 rounded-2xl border bg-card p-2 focus-within:border-primary focus-within:shadow-[var(--shadow-glow)]">
            <Search className="ml-2 h-5 w-5 shrink-0 text-muted-foreground" />
            <input
              value={query}
              onChange={(event) => setQuery(sanitizeSku(event.target.value))}
              placeholder="Введите номер детали (SKU / OEM)"
              inputMode="text"
              autoCapitalize="characters"
              className="min-w-0 flex-1 bg-transparent py-2 font-mono text-base outline-none placeholder:font-sans placeholder:text-muted-foreground"
            />
            <button type="button" aria-label="Сканировать номер" onClick={() => setScanOpen(true)} className="rounded-xl p-2 text-primary hover:bg-accent"><Camera className="h-5 w-5" /></button>
          </div>
          <button type="submit" disabled={!query} className="mt-3 w-full rounded-xl bg-primary py-3 font-semibold text-primary-foreground disabled:opacity-40">Найти</button>
        </form>
        <section className="mt-4 w-full max-w-md rounded-xl border bg-card p-4">
          <div className="flex items-start gap-3">
            <Wifi className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold">Проверка подключения к CatCar</h2>
              <p className="mt-1 text-xs text-muted-foreground">Проверка прямого доступа из браузера, без прокси. Поиск запчастей не затрагивается.</p>
              <button type="button" onClick={testDirectCatCarAccess} disabled={testingDirect}
                className="mt-3 inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-semibold hover:bg-muted disabled:opacity-50">
                {testingDirect ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wifi className="h-4 w-4" />}
                {testingDirect ? "Проверяю…" : "Проверить доступ"}
              </button>
              {directTest && (
                <div className={`mt-3 rounded-lg border p-3 text-xs ${directTest.status === "success" ? "border-green-500/40 bg-green-500/5" : directTest.status === "http-error" ? "border-amber-500/40 bg-amber-500/5" : "border-destructive/40 bg-destructive/5"}`}>
                  <p className="font-semibold">{directTest.message}</p>
                  <pre className="mt-2 whitespace-pre-wrap break-words text-muted-foreground">{directTest.details}</pre>
                </div>
              )}
            </div>
          </div>
        </section>
        <Link to="/catalog" className="mt-4 flex w-full max-w-md items-center justify-center gap-2 rounded-xl border bg-card px-4 py-3 text-sm font-semibold transition-colors hover:bg-muted">
          <CarFront className="h-4 w-4" />
          Открыть заводские каталоги автомобилей
        </Link>
      </div>
      <div className="m-4 flex h-16 items-center justify-center rounded-xl border border-dashed text-xs uppercase tracking-widest text-muted-foreground">Рекламный баннер</div>
      {scanOpen && (
        <Suspense fallback={<div className="p-6 text-center text-sm text-muted-foreground">Загрузка сканера…</div>}>
          <Scanner onClose={() => setScanOpen(false)} onResult={(sku) => { setScanOpen(false); go(sku); }} />
        </Suspense>
      )}
    </main>
  );
}
