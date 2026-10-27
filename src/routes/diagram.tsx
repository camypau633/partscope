import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { TransformWrapper, TransformComponent } from "react-zoom-pan-pinch";
import { ArrowLeft, Loader2, Share2, ZoomIn, ZoomOut, Maximize, X } from "lucide-react";
import { getPart, sanitizeSku, type Hotspot, type PartData } from "@/lib/parts";

export const Route = createFileRoute("/diagram")({
  validateSearch: (s: Record<string, unknown>) => ({ part: sanitizeSku(String(s["part"] ?? "")), car: String(s["car"] ?? "") }),
  head: () => ({
    meta: [
      { title: "Схема узла — PartScope" },
      { name: "description", content: "Интерактивная схема узла с OEM-номерами деталей." },
      { property: "og:title", content: "Схема узла — PartScope" },
      { property: "og:description", content: "Интерактивная схема узла с OEM-номерами деталей." },
    ],
  }),
  ssr: false,
  component: Diagram,
});

function Diagram() {
  const { part, car } = Route.useSearch();
  const { data } = useQuery({ queryKey: ["part", part], queryFn: () => getPart(part), staleTime: Infinity });
  const [active, setActive] = useState<Hotspot | null>(null);
  const [copied, setCopied] = useState(false);
  const [tab, setTab] = useState<"diagram" | "specs">("diagram");
  const vehicle = data?.applicability.find((v) => v.id === car);

  async function share() {
    const url = `${window.location.origin}${import.meta.env.BASE_URL}search?part=${encodeURIComponent(part)}`;
    const title = `${part} — ${vehicle ? `${vehicle.make} ${vehicle.model}` : "PartScope"}`;
    if (navigator.share) {
      try { await navigator.share({ title, url }); } catch { /* cancelled */ }
    } else {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }

  if (!data) return <div className="flex min-h-dvh items-center justify-center"><Loader2 className="h-6 w-6 animate-spin text-primary" /></div>;
  const { width, height, hotspots, imageUrl } = data.diagram;
  // The popover lives inside the SVG viewBox, so it stays attached to its point while zooming/panning.
  const popupWidth = Math.min(360, Math.max(220, width - 24));
  const popupHeight = 178;
  const popupX = active ? Math.max(12, Math.min(active.x - popupWidth / 2, width - popupWidth - 12)) : 0;
  const popupY = active ? (active.y > popupHeight + 24 ? active.y - popupHeight - 24 : Math.min(height - popupHeight - 12, active.y + 24)) : 0;
  const visibleSpecs = data.specs.slice(0, 3);

  return (
    <main className="flex h-dvh flex-col">
      <header className="flex items-center gap-3 border-b px-4 py-3">
        <Link to="/search" search={{ part }} aria-label="Назад" className="rounded-full p-2 hover:bg-muted"><ArrowLeft className="h-5 w-5" /></Link>
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold">{vehicle ? `${vehicle.make} ${vehicle.model}` : "Схема узла"}</div>
          <div className="truncate text-xs text-muted-foreground">{vehicle?.engine} · <span className="font-mono">{part}</span></div>
        </div>
        <button onClick={share} className="flex items-center gap-1 rounded-xl bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground">
          <Share2 className="h-4 w-4" /> {copied ? "Скопировано" : "Поделиться"}
        </button>
      </header>
      <div className="grid grid-cols-2 border-b">
        {([["diagram", "Взрыв-схема"], ["specs", "Технические характеристики"]] as const).map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)} className={`border-b-2 px-2 py-3 text-xs font-medium sm:text-sm ${tab === k ? "border-primary text-foreground" : "border-transparent text-muted-foreground"}`}>{l}</button>
        ))}
      </div>

      {tab === "specs" ? <SpecsTable data={data} /> : !imageUrl ? (
        <div className="flex flex-1 items-center justify-center p-8 text-center text-sm text-muted-foreground">Каталог не вернул схему для этой детали</div>
      ) : (
        <div className="relative flex-1 overflow-hidden bg-blueprint-paper" style={{ touchAction: "none" }}>
          <TransformWrapper minScale={0.6} maxScale={6} centerOnInit wheel={{ step: 0.15 }} pinch={{ step: 5 }}>
            {({ zoomIn, zoomOut, resetTransform }) => (
              <>
                <TransformComponent wrapperClass="!h-full !w-full" contentClass="!h-full !w-full !flex !items-center !justify-center">
                  <svg viewBox={`0 0 ${width} ${height}`} className="h-auto max-h-full w-full max-w-3xl" style={{ overflow: "visible" }}>
                    <image href={imageUrl} width={width} height={height} preserveAspectRatio="xMidYMid meet" />
                    {hotspots.map((h) => (
                      <g key={h.id} onClick={() => setActive(h)} className="cursor-pointer" role="button" aria-label={`Позиция ${h.id}: ${h.name}`}>
                        <circle cx={h.x} cy={h.y} r={h.radius} className="hotspot-ring fill-none stroke-primary" strokeWidth={2} />
                        <circle cx={h.x} cy={h.y} r={h.radius * 0.75} className={active?.id === h.id ? "fill-primary" : "fill-primary/80"} />
                        <text x={h.x} y={h.y + 8} textAnchor="middle" className="fill-primary-foreground text-[24px] font-bold">{h.id}</text>
                      </g>
                    ))}
                    {active && (
                      <foreignObject x={popupX} y={popupY} width={popupWidth} height={popupHeight} style={{ overflow: "visible", pointerEvents: "auto" }}>
                        <div xmlns="http://www.w3.org/1999/xhtml" className="h-full w-full overflow-hidden rounded-2xl border border-white/40 bg-slate-950/85 p-3 text-white shadow-2xl backdrop-blur-md">
                          <div className="flex items-start gap-2">
                            <div className="min-w-0 flex-1">
                              <div className="text-[11px] uppercase tracking-wider text-orange-300">Позиция {active.id} · OEM</div>
                              <div className="truncate text-sm font-bold">{active.name || "Деталь узла"}</div>
                              <div className="font-mono text-xs text-orange-200">{active.oem}</div>
                            </div>
                            <button type="button" aria-label="Закрыть подсказку" onClick={(event) => { event.stopPropagation(); setActive(null); }} className="rounded-full bg-white/10 p-1 hover:bg-white/20">
                              <X size={14} />
                            </button>
                          </div>
                          <div className="mt-2 border-t border-white/15 pt-2">
                            {visibleSpecs.length ? visibleSpecs.map((spec) => (
                              <div key={spec.key} className="flex items-baseline justify-between gap-2 text-xs leading-5">
                                <span className="text-white/70">{spec.label}</span>
                                <span className="shrink-0 font-mono font-semibold text-white">{spec.value}{spec.unit ? ` ${spec.unit}` : ""}</span>
                              </div>
                            )) : <div className="text-xs text-white/70">Размеры для этой позиции не найдены</div>}
                          </div>
                          <div className="mt-1 truncate text-[10px] text-white/45">Нажмите на другую точку, чтобы посмотреть характеристики</div>
                        </div>
                      </foreignObject>
                    )}
                  </svg>
                </TransformComponent>
                <div className="absolute right-3 top-3 flex flex-col gap-2">
                  {[[ZoomIn, () => zoomIn(), "Приблизить"], [ZoomOut, () => zoomOut(), "Отдалить"], [Maximize, () => { resetTransform(); setActive(null); }, "Сбросить"]].map(([Icon, fn, l], i) => {
                    const I = Icon as typeof ZoomIn;
                    return <button key={i} aria-label={l as string} onClick={fn as () => void} className="rounded-xl border bg-card/90 p-2"><I className="h-5 w-5" /></button>;
                  })}
                </div>
              </>
            )}
          </TransformWrapper>
          <p className="pointer-events-none absolute bottom-3 left-0 right-0 text-center text-xs text-blueprint-ink/60">Сведите пальцы для масштаба · нажмите на номер</p>
        </div>
      )}
    </main>
  );
}

function SpecsTable({ data }: { data: PartData }) {
  return (
    <div className="flex-1 overflow-y-auto p-4">
      <div className="mb-3 text-sm text-muted-foreground">{data.brand} · {data.name} · <span className="font-mono">{data.sku}</span></div>
      {!data.specs.length && <p className="text-sm text-muted-foreground">Характеристики не найдены</p>}
      <table className="w-full overflow-hidden rounded-xl border text-sm">
        <tbody className="divide-y">
          {data.specs.map((s) => (
            <tr key={s.key} className="odd:bg-card">
              <td className="px-3 py-3 text-muted-foreground">{s.label}</td>
              <td className="whitespace-nowrap px-3 py-3 text-right font-mono font-semibold">{s.value}{s.unit ? ` ${s.unit}` : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
