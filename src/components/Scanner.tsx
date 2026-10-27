import { useEffect, useRef, useState } from "react";
import { X, Loader2 } from "lucide-react";
import { sanitizeSku } from "@/lib/parts";

export function Scanner({ onResult, onClose }: { onResult: (sku: string) => void; onClose: () => void }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [status, setStatus] = useState("Запуск камеры…");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let stream: MediaStream | null = null;
    navigator.mediaDevices
      ?.getUserMedia({ video: { facingMode: "environment" } })
      .then((s) => {
        stream = s;
        if (videoRef.current) videoRef.current.srcObject = s;
        setStatus("Наведите на номер детали и нажмите «Сканировать»");
      })
      .catch(() => setStatus("Нет доступа к камере"));
    return () => stream?.getTracks().forEach((t) => t.stop());
  }, []);

  async function capture() {
    const v = videoRef.current;
    if (!v || !v.videoWidth) return;
    setBusy(true);
    setStatus("Распознавание…");
    const c = document.createElement("canvas");
    c.width = v.videoWidth;
    c.height = v.videoHeight;
    c.getContext("2d")!.drawImage(v, 0, 0);
    try {
      const { recognize } = await import("tesseract.js");
      const { data } = await recognize(c, "eng");
      const candidates = data.text.split(/\s+/).map(sanitizeSku).filter((t) => t.length >= 5 && /\d/.test(t));
      const best = candidates.sort((a, b) => b.length - a.length)[0];
      if (best) onResult(best);
      else setStatus("Номер не найден, попробуйте ещё раз");
    } catch {
      setStatus("Ошибка распознавания");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-background">
      <div className="flex items-center justify-between p-4">
        <span className="text-sm text-muted-foreground">{status}</span>
        <button aria-label="Закрыть" onClick={onClose} className="rounded-full p-2 hover:bg-muted"><X /></button>
      </div>
      <div className="relative flex-1 overflow-hidden">
        <video ref={videoRef} autoPlay playsInline muted className="h-full w-full object-cover" />
        <div className="pointer-events-none absolute inset-x-8 top-1/2 h-24 -translate-y-1/2 rounded-xl border-2 border-primary shadow-[var(--shadow-glow)]" />
      </div>
      <div className="p-6">
        <button onClick={capture} disabled={busy} className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary py-4 font-semibold text-primary-foreground disabled:opacity-60">
          {busy && <Loader2 className="h-5 w-5 animate-spin" />} Сканировать
        </button>
      </div>
    </div>
  );
}
