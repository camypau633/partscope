import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ArrowLeft, CarFront, ChevronRight, Home, Loader2, Search, Wifi } from "lucide-react";
import { fetchCatCarCatalogs, fetchCatCarPage, type CatCarBrowsePayload } from "@/lib/catcar";

type HistoryEntry = { url: string | null; title: string };

export const Route = createFileRoute("/catalog")({
  head: () => ({
    meta: [
      { title: "Заводские каталоги автомобилей — PartScope" },
      { name: "description", content: "Поиск автомобиля и заводских взрыв-схем в каталогах CatCar." },
    ],
  }),
  component: CatCarCatalog,
});

function CatCarCatalog() {
  const [currentUrl, setCurrentUrl] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [filter, setFilter] = useState("");
  const [isDirectTesting, setIsDirectTesting] = useState(false);
  const [directTest, setDirectTest] = useState<{ kind: "success" | "http-error" | "network-error"; message: string; details: string } | null>(null);

  async function testDirectCatCarAccess() {
    setIsDirectTesting(true);
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
        kind: response.ok ? "success" : "http-error",
        message: response.ok
          ? "Прямой доступ работает: браузер смог прочитать ответ CatCar."
          : `Браузер получил HTTP ${response.status} ${response.statusText} от CatCar.`,
        details: `HTTP: ${response.status} ${response.statusText}\nРазмер ответа: ${body.length} символов\nНачало ответа:\n${body.slice(0, 500)}`,
      });
    } catch (error) {
      const reason = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
      setDirectTest({
        kind: "network-error",
        message: "Браузер не смог прочитать ответ CatCar.",
        details: `${reason}\n\nЭто может быть ограничение CORS или сетевая ошибка. По одному этому результату нельзя точно отличить их друг от друга.`,
      });
    } finally {
      window.clearTimeout(timeout);
      setIsDirectTesting(false);
    }
  }


  const query = useQuery<CatCarBrowsePayload>({
    queryKey: ["catcar-browser", currentUrl],
    queryFn: () => currentUrl ? fetchCatCarPage(currentUrl) : fetchCatCarCatalogs(),
    staleTime: 10 * 60 * 1000,
    retry: 1,
  });

  const page = query.data;
  const filteredItems = (page?.items ?? []).filter((item) =>
    item.label.toLocaleLowerCase().includes(filter.trim().toLocaleLowerCase()),
  );

  function openItem(item: { label: string; url: string }) {
    setHistory((previous) => [...previous, { url: currentUrl, title: page?.title || "Каталоги" }]);
    setCurrentUrl(item.url);
    setFilter("");
  }

  function goBack() {
    const previous = history[history.length - 1];
    if (!previous) {
      setCurrentUrl(null);
    } else {
      setCurrentUrl(previous.url);
      setHistory((items) => items.slice(0, -1));
    }
    setFilter("");
  }

  function goHome() {
    setCurrentUrl(null);
    setHistory([]);
    setFilter("");
  }

  return (
    <main className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-10 border-b bg-background/95 px-4 py-3 backdrop-blur">
        <div className="mx-auto flex w-full max-w-3xl items-center gap-3">
          <button
            type="button"
            onClick={history.length ? goBack : goHome}
            aria-label="Назад"
            className="rounded-full p-2 hover:bg-muted"
          >
            <ArrowLeft className="h-5 w-5" />
          </button>
          <div className="min-w-0 flex-1">
            <h1 className="truncate font-semibold">Заводской каталог</h1>
            <p className="truncate text-xs text-muted-foreground">CatCar · марки, модели, модификации и схемы</p>
          </div>
          <button type="button" onClick={goHome} aria-label="К началу каталога" className="rounded-xl border p-2 hover:bg-muted">
            <Home className="h-4 w-4" />
          </button>
        </div>
      </header>

      <div className="mx-auto w-full max-w-3xl flex-1 space-y-4 p-4">
        <section className="rounded-2xl border bg-card p-4">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 text-muted-foreground"><Wifi className="h-5 w-5" /></div>
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold">Проверка прямого подключения к CatCar</h2>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                Тестирует запрос из браузера напрямую к CatCar, без прокси. Каталог и настройки приложения не меняются.
              </p>
              <button type="button" onClick={testDirectCatCarAccess} disabled={isDirectTesting}
                className="mt-3 inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60">
                {isDirectTesting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wifi className="h-4 w-4" />}
                {isDirectTesting ? "Проверяю…" : "Проверить доступ"}
              </button>
              {directTest && (
                <div className={`mt-3 rounded-xl border p-3 text-sm ${directTest.kind === "success" ? "border-green-500/40 bg-green-500/5" : directTest.kind === "http-error" ? "border-amber-500/40 bg-amber-500/5" : "border-destructive/40 bg-destructive/5"}`}>
                  <p className="font-semibold">{directTest.message}</p>
                  <pre className="mt-2 whitespace-pre-wrap break-words text-xs leading-relaxed text-muted-foreground">{directTest.details}</pre>
                </div>
              )}
            </div>
          </div>
        </section>

        <div className="rounded-2xl border bg-card p-4">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <CarFront className="h-4 w-4 text-primary" />
            {page?.title || (currentUrl ? "Загрузка раздела каталога…" : "Выберите производителя")}
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <button type="button" onClick={goHome} className="hover:text-foreground">Все марки</button>
            {history.map((entry, index) => (
              <span key={index} className="flex items-center gap-2">
                <ChevronRight className="h-3 w-3" />
                <span className="max-w-28 truncate">{entry.title}</span>
              </span>
            ))}
            {page?.title && <ChevronRight className="h-3 w-3" />}
            {page?.title && <span className="max-w-40 truncate text-foreground">{page.title}</span>}
          </div>
        </div>

        {query.isLoading && (
          <div className="space-y-3" role="status" aria-label="Загрузка каталога CatCar">
            {[0, 1, 2, 3, 4].map((item) => <div key={item} className="h-14 animate-pulse rounded-xl bg-muted" />)}
            <div className="flex items-center justify-center gap-2 py-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />Загрузка данных из CatCar…
            </div>
          </div>
        )}

        {query.isError && (
          <div className="rounded-xl border border-destructive/30 p-4 text-sm" role="alert">
            <p className="font-semibold">Не удалось загрузить каталог</p>
            <pre className="mt-2 whitespace-pre-wrap break-words rounded-lg bg-muted/60 p-3 font-mono text-xs leading-relaxed text-muted-foreground">{query.error instanceof Error ? query.error.message : "Прокси не вернул ответ."}</pre>
            <button type="button" onClick={() => query.refetch()} className="mt-3 rounded-lg border px-3 py-2 font-medium">Повторить</button>
          </div>
        )}

        {page && !query.isLoading && (
          <>
            {page.items.length > 8 && (
              <label className="flex items-center gap-2 rounded-xl border bg-card px-3 py-2">
                <Search className="h-4 w-4 text-muted-foreground" />
                <input
                  value={filter}
                  onChange={(event) => setFilter(event.target.value)}
                  placeholder="Фильтр по марке, модели или двигателю"
                  className="min-w-0 flex-1 bg-transparent text-sm outline-none"
                />
              </label>
            )}

            {filteredItems.length > 0 && (
              <section className="overflow-hidden rounded-xl border bg-card">
                <div className="border-b bg-muted/40 px-4 py-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {currentUrl ? "Доступные разделы" : "Производители"} · {filteredItems.length}
                </div>
                <ul className="divide-y">
                  {filteredItems.map((item) => (
                    <li key={item.url}>
                      <button
                        type="button"
                        onClick={() => openItem(item)}
                        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/60"
                      >
                        <span className="min-w-0 flex-1 text-sm font-medium">{item.label}</span>
                        <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {page.scheme_image && (
              <section className="space-y-3">
                <h2 className="font-semibold">Заводская схема</h2>
                <div className="flex min-h-48 items-center justify-center overflow-auto rounded-xl border bg-white p-2">
                  <a href={page.scheme_image} target="_blank" rel="noreferrer">
                    <img src={page.scheme_image} alt={page.title || "Заводская схема CatCar"} className="max-h-[70vh] max-w-full object-contain" />
                  </a>
                </div>
              </section>
            )}

            {page.parts.length > 0 && (
              <section className="space-y-3">
                <div className="flex items-center justify-between gap-3">
                  <h2 className="font-semibold">OEM и позиции на схеме</h2>
                  <span className="text-xs text-muted-foreground">{page.parts.length} записей</span>
                </div>
                <div className="overflow-hidden rounded-xl border">
                  <table className="w-full text-left text-sm">
                    <thead className="bg-muted/60 text-xs text-muted-foreground">
                      <tr><th className="px-3 py-3">Поз.</th><th className="px-3 py-3">OEM</th><th className="px-3 py-3">Наименование</th></tr>
                    </thead>
                    <tbody className="divide-y">
                      {page.parts.map((part, index) => (
                        <tr key={part.cleaned_oem + index} className="odd:bg-card">
                          <td className="whitespace-nowrap px-3 py-3 font-mono">{part.pos}</td>
                          <td className="whitespace-nowrap px-3 py-3">
                            <Link to="/search" search={{ part: part.cleaned_oem, partId: "" }} className="font-mono font-semibold text-primary hover:underline">
                              {part.raw_oem}
                            </Link>
                          </td>
                          <td className="px-3 py-3">{part.name}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            {!page.items.length && !page.scheme_image && !page.parts.length && (
              <div className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
                На этой странице не удалось распознать ссылки на следующий уровень каталога или схему.
              </div>
            )}
          </>
        )}
      </div>
    </main>
  );
}
