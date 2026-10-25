import { describe, expect, it } from "vitest";
import { normalizeRemote } from "./parts";

describe("normalizeRemote", () => {
  it("maps donor diagramUrl and specifications into the UI shape", () => {
    const r = normalizeRemote("9091901240", {
      analogues: [{ brand: "MANN", number: "W 68/3" }],
      applicability: [{ make: "Toyota", model: "Camry" }],
      specifications: [{ name: "Диаметр", value: 76, unit: "мм" }],
      diagramUrl: "https://x.test/a.jpg",
    });
    expect(r.diagramUrl).toBe("https://x.test/a.jpg");
    expect(r.specs[0]).toMatchObject({ label: "Диаметр", value: "76", unit: "мм" });
    expect(r.analogues[0]!.sku).toBe("W683");
  });
  it("rejects empty responses", () => {
    expect(() => normalizeRemote("X", {})).toThrow();
  });
});
