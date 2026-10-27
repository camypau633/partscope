import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ArrowLeft, ChevronRight, Database, Loader2 } from "lucide-react";
import { getPart, sanitizeSku } from "@/lib/parts";
import { TechnicalDrawings } from "@/components/TechnicalDrawings";

export const Route = createFileRoute("/search")({
  validateSearch: (search: Record<string, unknown>) => ({ part: sanitizeSku(String(search["part"] ?? "")) }),
  head: () => ({
    meta: [
      { title: "Результаты поиска — PartScope" },
      { name: "description", content: "Аналоги и применимость автозапчасти по номеру." },
      { property: "og:title", content: "Результаты поиска — PartScope" },
      { property: "og:description", content: "Аналоги и применимость автозапчасти по номеру." },
    ],
  }),
  ssr: false,
  component: Results,
});

function Results() {
  const { part } = Route.useSearch();
  const [tab, setTab] = useState<"analogues" | "apps" | "drawings">("analogues");
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["part", part],
    queryFn: () => getPart(part),
    enabled: !!part,
    staleTime: Infinity,
  });

  return (
    <main className="mx-auto min-h-dvh max-w-xl">
      <header className="sticky top-0 z-10 border-b bg-background/95 px-4 pb-0 pt-3 backdrop-blur">
        <div className="flex items-center gap-3">
          <Link to="/" aria-label="Назад" className="rounded-full p-2 hover:bg-muted"><ArrowLeft className="h-5 w-5" /></Link>
          <div className="min-w-0 flex-1">
            <div className="truncate font-mono text-lg font-bold">{part || "Поиск детали"}</div>
            {data && <div className="truncate text-xs text-muted-foreground">{data.brand} · {data.name}</div>}
          </div>
          {data && (
            <span className="ml-auto flex items-center gap-1 rounded-full bg-muted px-2 py-1 text-[10px] text-muted-foreground">
              {data.fromCache && <Database className="h-3 w-3" />}
              {data.fromCache ? "из кэша" : "каталог"}
            </span>
          )}
        </div>
        <div className="mt-3 grid grid-cols-3">
          {([
            ["analogues", "Аналоги"],
            ["apps", "Применимость"],
            ["drawings", "Чертежи и размеры"],
          ] as const).map(([key, label]) => (
            <button key={key} onClick={() => setTab(key)} className={`border-b-2 px-1 py-3 text-xs font-medium sm:text-sm ${tab === key ? "border-primary text-foreground" : "border-transparent text-muted-foreground"}`}>
              {label}
              {data && key === "analogues" && <span className="text-muted-foreground"> ({data.analogues.length})</span>}
              {data && key === "apps" && <span className="text-muted-foreground"> ({data.applicability.length})</span>}
              {data && key === "drawings" && <span className="text-muted-foreground"> ({data.technicalDrawings.length + data.specs.length})</span>}
            </button>
          ))}
        </div>
      </header>

      {isLoading && (
        <div className="space-y-3 p-4" aria-label="Загрузка каталога" role="status">
          {[0, 1, 2, 3].map((item) => <div key={item} className="h-16 animate-pulse rounded-xl bg-muted" />)}
          <div className="flex items-center justify-center gap-2 py-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Загрузка данных каталога…</div>
        </div>
      )}

      {!part && <p className="p-8 text-center text-sm text-muted-foreground">Введите номер детали на главном экране.</p>}

      {isError && (
        <div className="p-8 text-center" role="alert">
          <p className="font-semibold">Не удалось получить данные из каталога</p>
          <p className="mt-2 text-sm text-muted-foreground">{error instanceof Error ? error.message : "Источник данных недоступен или не вернул данные для этого номера."}</p>
          <Link to="/" className="mt-4 inline-block rounded-xl border px-4 py-2 text-sm">Вернуться к поиску</Link>
        </div>
      )}

      {data && tab === "analogues" && (
        data.analogues.length ? (
          <ul className="divide-y">
            {data.analogues.map((analogue) => (
              <li key={`${analogue.brand}:${analogue.sku}`} className="flex items-center gap-3 px-4 py-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent text-[10px] font-bold text-accent-foreground">{analogue.brand.slice(0, 4)}</div>
                <div className="min-w-0 flex-1">
                  <Link to="/search" search={{ part: analogue.sku }} className="font-mono font-semibold hover:text-primary">{analogue.sku}</Link>
                  <div className="truncate text-xs text-muted-foreground">{analogue.brand}{analogue.name ? ` · ${analogue.name}` : ""}</div>
                </div>
                <button
                  type="button"
                  onClick={() => window.open(`https://www.google.com/search?q=${encodeURIComponent(`${analogue.brand} ${analogue.sku} ${analogue.name} купить`)}`, "_blank", "noopener,noreferrer")}
                  className="rounded-lg bg-amber-500 px-4 py-2 text-sm font-semibold text-black transition-colors hover:bg-amber-600"
                >
                  Где купить
                </button>
              </li>
            ))}
          </ul>
        ) : <p className="p-8 text-center text-sm text-muted-foreground">Источник не предоставил аналоги для этого номера.</p>
      )}

      {data && tab === "apps" && (
        data.applicability.length ? (
          <ul className="divide-y">
            {data.applicability.map((vehicle) => (
              <li key={vehicle.id}>
                <Link to="/diagram" search={{ part, car: vehicle.id }} className="flex items-center gap-3 px-4 py-3 hover:bg-muted">
                  <div className="min-w-0 flex-1">
                    <div className="font-semibold">{vehicle.make} {vehicle.model}</div>
                    <div className="text-xs text-muted-foreground">{[vehicle.engine, vehicle.years].filter(Boolean).join(" · ")}</div>
                  </div>
                  <ChevronRight className="h-5 w-5 text-muted-foreground" />
                </Link>
              </li>
            ))}
          </ul>
        ) : <p className="p-8 text-center text-sm text-muted-foreground">Источник не предоставил данные о применимости для этого номера.</p>
      )}
      {data && tab === "drawings" && <TechnicalDrawings data={data} />}
    </main>
  );
}
