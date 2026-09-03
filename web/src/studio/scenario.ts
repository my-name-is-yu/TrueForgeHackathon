import type {
  ScenarioFrame,
  ScenarioPlaybackFrame,
  ScenarioPreview,
} from "./types";

const interpolate = (from: number, to: number, progress: number): number => (
  from + ((to - from) * progress)
);

export function sampleScenario(
  scenario: ScenarioPreview,
  elapsedS: number,
): ScenarioPlaybackFrame {
  const timeS = Math.max(0, Math.min(elapsedS, scenario.durationS));
  let left = scenario.keyframes[0];
  let right = scenario.keyframes[scenario.keyframes.length - 1];

  for (let index = 1; index < scenario.keyframes.length; index += 1) {
    if (scenario.keyframes[index].timeS >= timeS) {
      right = scenario.keyframes[index];
      left = scenario.keyframes[index - 1];
      break;
    }
    left = scenario.keyframes[index];
  }

  const span = right.timeS - left.timeS;
  const progressBetweenFrames = span <= 0 ? 0 : (timeS - left.timeS) / span;
  const frame: ScenarioFrame = {
    timeS,
    wheels: {
      leftCommand: interpolate(
        left.wheels.leftCommand,
        right.wheels.leftCommand,
        progressBetweenFrames,
      ),
      rightCommand: interpolate(
        left.wheels.rightCommand,
        right.wheels.rightCommand,
        progressBetweenFrames,
      ),
    },
    neck: {
      panDeg: interpolate(left.neck.panDeg, right.neck.panDeg, progressBetweenFrames),
      tiltDeg: interpolate(left.neck.tiltDeg, right.neck.tiltDeg, progressBetweenFrames),
    },
    face: {
      expression: progressBetweenFrames >= 1 ? right.face.expression : left.face.expression,
    },
    soundCue: progressBetweenFrames >= 1 ? right.soundCue : left.soundCue,
  };
  return {
    ...frame,
    progress: scenario.durationS === 0 ? 1 : timeS / scenario.durationS,
  };
}
