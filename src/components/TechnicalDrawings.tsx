import { useState } from "react";
import { TransformComponent, TransformWrapper } from "react-zoom-pan-pinch";
import { Maximize2, ChevronLeft, ChevronRight, X, ZoomIn, ZoomOut, RotateCcw, Image as ImageIcon } from "lucide-react";
import type { PartData } from "@/lib/parts";

export function TechnicalDrawings({ data }: { data: PartData }) {
  const drawings = data.technicalDrawings ?? [];
  const [activeIndex, setActiveIndex] = useState(0);
  const [fullScreen, setFullScreen] = useState(false);
  const [failedImages, setFailedImages] = useState<string[]>([]);
  const availableDrawings = drawings.filter((url) => !failedImages.includes(url));
  const activeUrl = availableDrawings[Math.min(activeIndex, Math.max(availableDrawings.length - 1, 0))];

  const specs = data.specs ?? [];

  function markFailed(url: string) {
    setFailedImages((previous) => previous.includes(url) ? previous : [...previous, url]);
    setActiveIndex(0);
  }

  return (
    <section className="space-y-4 p-4" aria-label="Чертежи и размеры">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-3">
          <div className="flex items-center justify-between gap-2">
            <h2 className="font-semibold">Чертежи детали</h2>
            {availableDrawings.length > 0 && <span className="text-xs text-muted-foreground">{Math.min(activeIndex + 1, availableDrawings.length)} / {availableDrawings.length}</span>}
          </div>

          {activeUrl ? (
            <>
              <button
                type="button"
                onClick={() => setFullScreen(true)}
                className="group relative flex min-h-64 w-full items-center justify-center overflow-hidden rounded-2xl border bg-card p-2 text-left"
                aria-label="Открыть чертёж на весь экран"
              >
                <img
                  src={activeUrl}
                  alt={`Чертёж детали ${data.sku}`}
                  className="max-h-[55vh] w-full object-contain"
                  onError={() => markFailed(activeUrl)}
                />
                <span className="absolute bottom-3 right-3 inline-flex items-center gap-1 rounded-lg border bg-background/90 px-3 py-2 text-xs font-medium shadow-sm">
                  <Maximize2 className="h-4 w-4" /> Увеличить
                </span>
              </button>
              {availableDrawings.length > 1 && (
                <div className="flex items-center justify-between gap-3">
                  <button type="button" onClick={() => setActiveIndex((i) => (i - 1 + availableDrawings.length) % availableDrawings.length)} className="rounded-lg border p-2" aria-label="Предыдущий чертёж"><ChevronLeft className="h-5 w-5" /></button>
                  <div className="flex min-w-0 flex-1 gap-2 overflow-x-auto py-1">
                    {availableDrawings.map((url, index) => (
                      <button key={url} type="button" onClick={() => setActiveIndex(index)} className={`h-16 w-20 shrink-0 overflow-hidden rounded-lg border p-1 ${index === activeIndex ? "border-primary ring-1 ring-primary" : "border-border"}`} aria-label={`Показать чертёж ${index + 1}`}>
                        <img src={url} alt="" className="h-full w-full object-contain" loading="lazy" />
                      </button>
                    ))}
                  </div>
                  <button type="button" onClick={() => setActiveIndex((i) => (i + 1) % availableDrawings.length)} className="rounded-lg border p-2" aria-label="Следующий чертёж"><ChevronRight className="h-5 w-5" /></button>
                </div>
              )}
            </>
          ) : (
            <div className="flex min-h-64 flex-col items-center justify-center rounded-2xl border border-dashed bg-muted/30 p-6 text-center">
              <ImageIcon className="mb-3 h-9 w-9 text-muted-foreground" />
              <p className="font-medium">Чертёж в процессе оцифровки</p>
              <p className="mt-2 max-w-xs text-sm text-muted-foreground">Чертёж для артикула <span className="font-mono font-semibold">{data.sku}</span> находится в процессе оцифровки.</p>
            </div>
          )}
          <p className="text-xs text-muted-foreground">Наличие изображения зависит от подключённого источника данных. Проверяйте размеры по официальной документации перед установкой.</p>
        </div>

        <div className="min-w-0">
          <h2 className="mb-3 font-semibold">Размеры и критерии</h2>
          {specs.length ? (
            <div className="overflow-hidden rounded-xl border">
              <table className="w-full text-sm">
                <tbody className="divide-y">
                  {specs.map((spec) => (
                    <tr key={spec.key || spec.label} className="odd:bg-card">
                      <th scope="row" className="w-3/5 px-3 py-3 text-left font-normal text-muted-foreground">{spec.label || spec.key}</th>
                      <td className="px-3 py-3 text-right font-mono font-semibold break-words">{spec.value}{spec.unit ? ` ${spec.unit}` : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="rounded-xl border border-dashed p-5 text-sm text-muted-foreground">Точные размеры и технические критерии пока не предоставлены источником для артикула {data.sku}.</div>
          )}
        </div>
      </div>

      {fullScreen && activeUrl && (
        <div className="fixed inset-0 z-50 flex flex-col bg-black/95 text-white" role="dialog" aria-modal="true" aria-label="Чертёж во весь экран">
          <div className="flex items-center gap-3 border-b border-white/15 px-3 py-3">
            <div className="min-w-0 flex-1">
              <div className="font-semibold">Чертежи и размеры</div>
              <div className="font-mono text-xs text-white/60">{data.sku}</div>
            </div>
            <button type="button" onClick={() => setFullScreen(false)} className="rounded-lg bg-white/10 p-2" aria-label="Закрыть полноэкранный режим"><X className="h-5 w-5" /></button>
          </div>
          <div className="relative min-h-0 flex-1" style={{ touchAction: "none" }}>
            <TransformWrapper key={activeUrl} minScale={0.5} maxScale={8} centerOnInit wheel={{ step: 0.2 }} pinch={{ step: 5 }}>
              {({ zoomIn, zoomOut, resetTransform }) => (
                <>
                  <TransformComponent wrapperClass="!h-full !w-full" contentClass="!h-full !w-full !flex !items-center !justify-center">
                    <img src={activeUrl} alt={`Увеличенный чертёж ${data.sku}`} className="max-h-full max-w-full object-contain" />
                  </TransformComponent>
                  <div className="absolute right-3 top-3 flex flex-col gap-2">
                    <button type="button" onClick={() => zoomIn()} className="rounded-lg border border-white/20 bg-black/70 p-2" aria-label="Увеличить"><ZoomIn className="h-5 w-5" /></button>
                    <button type="button" onClick={() => zoomOut()} className="rounded-lg border border-white/20 bg-black/70 p-2" aria-label="Уменьшить"><ZoomOut className="h-5 w-5" /></button>
                    <button type="button" onClick={() => resetTransform()} className="rounded-lg border border-white/20 bg-black/70 p-2" aria-label="Сбросить масштаб"><RotateCcw className="h-5 w-5" /></button>
                  </div>
                </>
              )}
            </TransformWrapper>
          </div>
          <p className="px-4 py-3 text-center text-xs text-white/60">Разведите пальцы для увеличения · перетаскивайте чертёж для перемещения</p>
        </div>
      )}
    </section>
  );
}
