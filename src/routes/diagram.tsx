import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { TransformWrapper, TransformComponent } from "react-zoom-pan-pinch";
import { ArrowLeft, Loader2, Share2, ZoomIn, ZoomOut, Maximize, X } from "lucide-react";
import { getPart, sanitizeSku, type Hotspot, type PartData } from "@/lib/parts";
import { fetchCatCarPage } from "@/lib/catcar";
import { TechnicalDrawings } from "@/components/TechnicalDrawings";

export const Route = createFileRoute("/diagram")({
  validateSearch: (s: Record<string, unknown>) => ({ part: sanitizeSku(String(s["part"] ?? "")), partId: String(s["partId"] ?? ""), car: String(s["car"] ?? ""), catcarUrl: String(s["catcarUrl"] ?? "") }),
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
  const { part, partId, car, catcarUrl } = Route.useSearch();
  const { data, isLoading, isError, error } = useQuery({ queryKey: ["part", part, partId], queryFn: () => getPart(part, partId), enabled: !!part, staleTime: Infinity });
  const catcarQuery = useQuery({ queryKey: ["catcar-page", catcarUrl], queryFn: () => fetchCatCarPage(catcarUrl), enabled: !!catcarUrl, staleTime: 5 * 60 * 1000 });
  const [active, setActive] = useState<Hotspot | null>(null);
  const [copied, setCopied] = useState(false);
  const [tab, setTab] = useState<"diagram" | "specs" | "drawings">("diagram");
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

  if (isLoading) return <main className="flex min-h-dvh flex-col items-center justify-center gap-4 p-6" role="status"><Loader2 className="h-7 w-7 animate-spin text-primary" /><p className="text-sm text-muted-foreground">Загрузка данных детали и схемы…</p><div className="w-full max-w-md space-y-3"><div className="h-24 animate-pulse rounded-xl bg-muted" /><div className="h-48 animate-pulse rounded-xl bg-muted" /></div></main>;
  if (isError || !data) return <main className="flex min-h-dvh flex-col items-center justify-center gap-3 p-6 text-center" role="alert"><p className="font-semibold">Данные детали недоступны</p><p className="max-w-md text-sm text-muted-foreground">{error instanceof Error ? error.message : part ? "Источник не вернул данные для этого номера." : "Вернитесь на главный экран и введите номер детали."}</p><Link to="/" className="mt-2 rounded-xl border px-4 py-2 text-sm">Вернуться к поиску</Link></main>;
  const { width, height, hotspots, imageUrl } = data.diagram;
  // The popover lives inside the SVG viewBox, so it stays attached to its point while zooming/panning.
  const popupWidth = Math.min(360, Math.max(220, width - 24));
  const popupHeight = 178;
  const popupX = active ? Math.max(12, Math.min(active.x - popupWidth / 2, width - popupWidth - 12)) : 0;
  const popupY = active ? (active.y > popupHeight + 24 ? active.y - popupHeight - 24 : Math.min(height - popupHeight - 12, active.y + 24)) : 0;
  const visibleSpecs = (active?.specs?.length ? active.specs : active?.oem === data.sku ? data.specs : []).slice(0, 3);

  return (
    <main className="flex h-dvh flex-col">
      <header className="flex items-center gap-3 border-b px-4 py-3">
        <Link to="/search" search={{ part, partId }} aria-label="Назад" className="rounded-full p-2 hover:bg-muted"><ArrowLeft className="h-5 w-5" /></Link>
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold">{vehicle ? `${vehicle.make} ${vehicle.model}` : "Схема узла"}</div>
          <div className="truncate text-xs text-muted-foreground">{vehicle?.engine} · <span className="font-mono">{part}</span></div>
        </div>
        <button onClick={share} className="flex items-center gap-1 rounded-xl bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground">
          <Share2 className="h-4 w-4" /> {copied ? "Скопировано" : "Поделиться"}
        </button>
      </header>
      <div className="grid grid-cols-3 border-b">
        {([
          ["diagram", "Взрыв-схема"],
          ["specs", "Характеристики"],
          ["drawings", "Чертежи и размеры"],
        ] as const).map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)} className={`border-b-2 px-1 py-3 text-[11px] font-medium sm:text-sm ${tab === k ? "border-primary text-foreground" : "border-transparent text-muted-foreground"}`}>{l}</button>
        ))}
      </div>

      {tab === "specs" ? <SpecsTable data={data} /> : tab === "drawings" ? <TechnicalDrawings data={data} /> : catcarUrl ? (
        <section className="flex-1 space-y-4 overflow-y-auto p-4" aria-label="Заводская схема CatCar">
          {catcarQuery.isLoading && (
            <div className="space-y-3" role="status" aria-label="Загрузка заводской схемы">
              <div className="h-72 animate-pulse rounded-xl bg-muted" />
              {[0, 1, 2].map((item) => <div key={item} className="h-12 animate-pulse rounded-xl bg-muted" />)}
              <p className="text-center text-sm text-muted-foreground">Загрузка схемы и списка деталей CatCar…</p>
            </div>
          )}
          {catcarQuery.isError && (
            <div className="rounded-xl border border-destructive/30 p-4 text-sm" role="alert">
              <p className="font-semibold">Не удалось загрузить схему CatCar</p>
              <p className="mt-1 text-muted-foreground">{catcarQuery.error instanceof Error ? catcarQuery.error.message : "Прокси не вернул данные."}</p>
              <button type="button" onClick={() => catcarQuery.refetch()} className="mt-3 rounded-lg border px-3 py-2 font-medium">Повторить</button>
            </div>
          )}
          {catcarQuery.data && (
            <>
              {catcarQuery.data.scheme_image ? (
                <div className="flex min-h-56 items-center justify-center overflow-auto rounded-xl border bg-white p-2">
                  <img src={catcarQuery.data.scheme_image} alt="Заводская взрыв-схема CatCar" className="max-h-[65vh] max-w-full object-contain" />
                </div>
              ) : (
                <div className="rounded-xl border border-dashed p-5 text-center text-sm text-muted-foreground">Изображение схемы отсутствует в ответе каталога.</div>
              )}
              <div className="flex items-center justify-between gap-3">
                <h2 className="font-semibold">Детали на схеме</h2>
                <span className="text-xs text-muted-foreground">{catcarQuery.data.parts.length} позиций</span>
              </div>
              {catcarQuery.data.parts.length ? (
                <div className="overflow-hidden rounded-xl border">
                  <table className="w-full text-left text-sm">
                    <thead className="bg-muted/60 text-xs text-muted-foreground">
                      <tr><th className="px-3 py-3">Поз.</th><th className="px-3 py-3">OEM</th><th className="px-3 py-3">Наименование</th></tr>
                    </thead>
                    <tbody className="divide-y">
                      {catcarQuery.data.parts.map((item, index) => (
                        <tr key={`${item.cleaned_oem}:${item.pos}:${index}`} className="odd:bg-card">
                          <td className="whitespace-nowrap px-3 py-3 font-mono">{item.pos}</td>
                          <td className="whitespace-nowrap px-3 py-3">
                            <Link to="/search" search={{ part: item.cleaned_oem, partId: "" }} className="font-mono font-semibold text-primary hover:underline">{item.cleaned_oem}</Link>
                            {item.raw_oem !== item.cleaned_oem && <div className="text-[11px] text-muted-foreground">{item.raw_oem}</div>}
                          </td>
                          <td className="px-3 py-3">{item.name}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="rounded-xl border border-dashed p-5 text-center text-sm text-muted-foreground">В ответе CatCar нет распознанных OEM-позиций.</p>
              )}
              <p className="text-xs text-muted-foreground">Нажатие на очищенный OEM открывает поиск детали и доступные перекрёстные ссылки PartScope.</p>
            </>
          )}
        </section>
      ) : !imageUrl ? (
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
                            )) : <div className="text-xs text-white/70">Размеры этой позиции не переданы источником данных</div>}
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
