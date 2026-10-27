import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ArrowLeft, ChevronRight, Database, Loader2, WifiOff } from "lucide-react";
import { getPart, sanitizeSku } from "@/lib/parts";

export const Route = createFileRoute("/search")({
  validateSearch: (s: Record<string, unknown>) => ({ part: sanitizeSku(String(s['part'] ?? "")) }),
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
  const [tab, setTab] = useState<"analogues" | "apps">("analogues");
  const { data, isLoading } = useQuery({ queryKey: ["part", part], queryFn: () => getPart(part), enabled: !!part, staleTime: Infinity });

  return (
    <main className="mx-auto min-h-dvh max-w-xl">
      <header className="sticky top-0 z-10 border-b bg-background/95 px-4 pb-0 pt-3 backdrop-blur">
        <div className="flex items-center gap-3">
          <Link to="/" aria-label="Назад" className="rounded-full p-2 hover:bg-muted"><ArrowLeft className="h-5 w-5" /></Link>
          <div className="min-w-0">
            <div className="truncate font-mono text-lg font-bold">{part}</div>
            {data && <div className="truncate text-xs text-muted-foreground">{data.brand} · {data.name}</div>}
          </div>
          {data && (
            <span className="ml-auto flex items-center gap-1 rounded-full bg-muted px-2 py-1 text-[10px] text-muted-foreground">
              {data.fromCache ? <Database className="h-3 w-3" /> : data.source === "demo" ? <WifiOff className="h-3 w-3" /> : null}
              {data.fromCache ? "из кэша" : data.source === "demo" ? "демо" : "сеть"}
            </span>
          )}
        </div>
        <div className="mt-3 grid grid-cols-2">
          {([["analogues", "Аналоги"], ["apps", "Применимость"]] as const).map(([k, l]) => (
            <button key={k} onClick={() => setTab(k)} className={`border-b-2 py-3 text-sm font-medium ${tab === k ? "border-primary text-foreground" : "border-transparent text-muted-foreground"}`}>
              {l} {data && <span className="text-muted-foreground">({k === "analogues" ? data.analogues.length : data.applicability.length})</span>}
            </button>
          ))}
        </div>
      </header>

      {isLoading && <div className="flex justify-center p-12"><Loader2 className="h-6 w-6 animate-spin text-primary" /></div>}

      {data && tab === "analogues" && (
        <ul className="divide-y">
          {data.analogues.map((a, i) => (
            <li key={i} className="flex items-center gap-3 px-4 py-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent text-[10px] font-bold text-accent-foreground">{a.brand.slice(0, 4)}</div>
              <div className="min-w-0 flex-1">
                <Link to="/search" search={{ part: a.sku }} className="font-mono font-semibold hover:text-primary">{a.sku}</Link>
                <div className="truncate text-xs text-muted-foreground">{a.brand} · {a.name}</div>
              </div>
              {a.price && <div className="font-mono text-sm">{a.price.toLocaleString("ru-RU")} ₽</div>}
            </li>
          ))}
        </ul>
      )}

      {data && tab === "apps" && (
        <ul className="divide-y">
          {data.applicability.map((v) => (
            <li key={v.id}>
              <Link to="/diagram" search={{ part, car: v.id }} className="flex items-center gap-3 px-4 py-3 hover:bg-muted">
                <div className="min-w-0 flex-1">
                  <div className="font-semibold">{v.make} {v.model}</div>
                  <div className="text-xs text-muted-foreground">{v.engine} · {v.years}</div>
                </div>
                <ChevronRight className="h-5 w-5 text-muted-foreground" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
