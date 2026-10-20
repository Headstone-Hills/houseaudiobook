import { describe, expect, test } from "vitest";
import { FUNCTIONS } from "../test-utils";
import { describeFormat, formatBytes, formatDuration } from "./format";
import { dependents, explain, prerequisites, toggle } from "./functions";
import { pickLevel, toColumns } from "./peaks";

describe("function dependencies", () => {
  test("prerequisites and dependents are transitive", () => {
    expect(prerequisites(FUNCTIONS, "flubs")).toEqual(["transcribe", "compare_text"]);
    expect(dependents(FUNCTIONS, "transcribe")).toEqual(["compare_text", "cleanup", "flubs", "takes"]);
  });

  test("turning on adds prerequisites; turning off removes dependents", () => {
    expect(toggle(FUNCTIONS, ["silences"], "takes", true)).toEqual([
      "transcribe",
      "compare_text",
      "silences",
      "takes",
    ]);
    const all = FUNCTIONS.map((f) => f.key);
    expect(toggle(FUNCTIONS, all, "compare_text", false)).toEqual(["transcribe", "organize", "cleanup", "silences"]);
  });

  test("explain names prerequisites and the manuscript need", () => {
    expect(explain(FUNCTIONS, "compare_text")).toBe("Requires Transcribe; needs an imported manuscript");
    expect(explain(FUNCTIONS, "silences")).toBe("");
  });
});

describe("peaks", () => {
  const s = { base: 512, factor: 4, levels: 4 };

  test("pickLevel takes the coarsest level with a bucket per pixel", () => {
    expect(pickLevel(10 * 3600 * 44100, 1200, s)).toBe(3); // 10 h: coarsest is plenty
    expect(pickLevel(60 * 44100, 1200, s)).toBe(1); // 1 min: 1292 buckets at level 1
    expect(pickLevel(1000, 1200, s)).toBe(0); // shorter than the screen
  });

  test("toColumns keeps the envelope and fills every column", () => {
    const cols = toColumns([-100, -32767, 0, -10], [100, 50, 32767, 10], 2);
    // Columns are Float32Array, so compare against float32-rounded values.
    expect(Array.from(cols.min)).toEqual([-1, Math.fround(-10 / 32767)]);
    expect(Array.from(cols.max)).toEqual([Math.fround(100 / 32767), 1]);
    const stretched = toColumns([-32767], [32767], 3); // fewer buckets than pixels
    expect(Array.from(stretched.max)).toEqual([1, 1, 1]);
    expect(toColumns([], [], 4).max.length).toBe(4);
  });
});

describe("format", () => {
  test("durations, sizes, formats", () => {
    expect(formatDuration(65)).toBe("1:05.0");
    expect(formatDuration(3725.54)).toBe("1:02:05.5");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1_572_864)).toBe("1.5 MB");
    expect(
      describeFormat({ format: "mp3", sample_rate: 44100, channels: 2, bit_depth: null, bit_rate: 128000 }),
    ).toBe("MP3 · 44.1 kHz · 128 kbps · stereo");
  });
});
