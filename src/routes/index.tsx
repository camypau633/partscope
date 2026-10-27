import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { lazy, Suspense, useState } from "react";
import { Camera, Search } from "lucide-react";
import { sanitizeSku } from "@/lib/parts";

const Scanner = lazy(() => import("@/components/Scanner").then((m) => ({ default: m.Scanner })));

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "PartScope — поиск автозапчастей и аналогов" },
      { name: "description", content: "Поиск автозапчастей по OEM-номеру, аналоги, применимость и интерактивные схемы. Работает офлайн." },
      { property: "og:title", content: "PartScope — поиск автозапчастей" },
      { property: "og:description", content: "Аналоги, применимость и схемы узлов по номеру детали." },
    ],
  }),
  component: Home,
});

function Home() {
  const [q, setQ] = useState("");
  const [scan, setScan] = useState(false);
  const navigate = useNavigate();
  const go = (sku: string) => sku && navigate({ to: "/search", search: { part: sku } });

  return (
    <main className="grid-bg flex min-h-dvh flex-col">
      <div className="flex flex-1 flex-col items-center justify-center px-5">
        <img src={`${import.meta.env.BASE_URL}icon-192.png`} alt="" width={64} height={64} className="mb-5 rounded-2xl" />
        <h1 className="text-3xl font-bold tracking-tight">PartScope</h1>
        <p className="mb-8 mt-1 text-sm text-muted-foreground">Аналоги · Применимость · Схемы</p>
        <form onSubmit={(e) => { e.preventDefault(); go(q); }} className="w-full max-w-md">
          <div className="flex items-center gap-2 rounded-2xl border bg-card p-2 focus-within:border-primary focus-within:shadow-[var(--shadow-glow)]">
            <Search className="ml-2 h-5 w-5 shrink-0 text-muted-foreground" />
            <input
              value={q}
              onChange={(e) => setQ(sanitizeSku(e.target.value))}
              placeholder="Номер детали, напр. 9091901240"
              inputMode="text"
              autoCapitalize="characters"
              className="min-w-0 flex-1 bg-transparent py-2 font-mono text-base outline-none placeholder:font-sans placeholder:text-muted-foreground"
            />
            <button type="button" aria-label="Сканировать номер" onClick={() => setScan(true)} className="rounded-xl p-2 text-primary hover:bg-accent"><Camera className="h-5 w-5" /></button>
          </div>
          <button type="submit" disabled={!q} className="mt-3 w-full rounded-xl bg-primary py-3 font-semibold text-primary-foreground disabled:opacity-40">Найти</button>
        </form>
        <div className="mt-6 flex flex-wrap justify-center gap-2 text-xs">
          {["9091901240", "04152YZZA1", "OC90"].map((s) => (
            <button key={s} onClick={() => go(s)} className="rounded-full border px-3 py-1 font-mono text-muted-foreground hover:text-foreground">{s}</button>
          ))}
        </div>
      </div>
      <div className="m-4 flex h-16 items-center justify-center rounded-xl border border-dashed text-xs uppercase tracking-widest text-muted-foreground">Рекламный баннер</div>
      {scan && (
        <Suspense fallback={null}>
          <Scanner onClose={() => setScan(false)} onResult={(s) => { setScan(false); go(s); }} />
        </Suspense>
      )}
    </main>
  );
}
