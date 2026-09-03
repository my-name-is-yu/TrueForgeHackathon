import { describe, expect, it } from "vitest";

import { sampleScenario } from "../src/studio/scenario";

describe("sampleScenario", () => {
  it("interpolates normalized wheel commands while holding discrete face and sound states", () => {
    const scenario = {
      scenarioId: "greet",
      durationS: 2,
      evidenceLevel: "concept_only" as const,
      keyframes: [
        {
          timeS: 0,
          wheels: { leftCommand: 0, rightCommand: 0 },
          neck: { panDeg: 0, tiltDeg: 0 },
          face: { expression: "neutral" },
          soundCue: "soft_chirp",
        },
        {
          timeS: 2,
          wheels: { leftCommand: 0.4, rightCommand: -0.2 },
          neck: { panDeg: 20, tiltDeg: -10 },
          face: { expression: "happy" },
          soundCue: null,
        },
      ],
    };

    expect(sampleScenario(scenario, 1)).toEqual({
      timeS: 1,
      progress: 0.5,
      wheels: { leftCommand: 0.2, rightCommand: -0.1 },
      neck: { panDeg: 10, tiltDeg: -5 },
      face: { expression: "neutral" },
      soundCue: "soft_chirp",
    });
    expect(sampleScenario(scenario, 3).face.expression).toBe("happy");
  });
});
