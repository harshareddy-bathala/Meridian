// Every settled pass, decoded and put into words (D-365).

import { describe, expect, it } from "vitest";

import { resultBody } from "./fixtures.test-data";
import { classWords, decodeResult, effectWords } from "./results";
import { mergeOnce } from "./useOlderPages";

describe("a settled pass", () => {
  it("is decoded with its effect, which the platform reads, not the page", () => {
    const lost = decodeResult(resultBody(2, "station_unavailable", "lost"), "r");
    expect(lost.effect).toBe("lost");
    expect(lost.cause).toBe("obstruction");
    expect(() => decodeResult(resultBody(3, "confirmed_miss", "spent"), "r")).toThrow("r.effect");
  });

  it("is put into words", () => {
    expect(classWords("signal_no_decode")).toBe("Heard, not decoded");
    expect(effectWords("satellite")).toBe("the satellite's own: counts neither way");
    expect(effectWords("lost")).toBe("lost: spends the loss budget");
  });

  it("is listed once when an older page overlaps the newest", () => {
    const newest = [3, 2].map((id) => decodeResult(resultBody(id, "successful_reception", "captured"), "r"));
    const older = [2, 1].map((id) => decodeResult(resultBody(id, "successful_reception", "captured"), "r"));
    expect(mergeOnce(newest, older, (one) => String(one.classificationId)).map((one) => one.classificationId)).toEqual([
      3, 2, 1,
    ]);
  });
});
