import { describe, expect, it } from "vitest";
import { sanitizeSku } from "./parts";

describe("sanitizeSku", () => {
  it("strips dashes and uppercases", () => {
    expect(sanitizeSku("90919-01240")).toBe("9091901240");
  });
  it("strips spaces, dots and special characters", () => {
    expect(sanitizeSku(" oc 90.1/a ")).toBe("OC901A");
  });
});
