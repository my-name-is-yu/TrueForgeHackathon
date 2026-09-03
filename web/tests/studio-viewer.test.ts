import * as THREE from "three";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CharacterSpecView, ScenarioPlaybackFrame } from "../src/studio/types";
import {
  applyStudioRigFrame,
  createFaceExpressionTexture,
  hideDiagnosticGeometry,
  partLocalBounds,
  prefersReducedMotion,
  rigStudioModel,
} from "../src/studio/viewer";

const spec = (): CharacterSpecView => ({
  name: "Pico",
  role: "Guide",
  motif: "duck",
  designBrief: "A cautious duck guide.",
  hardwareProfileId: "m5-cores3-goplus2/v1",
  morphologyNodes: [
    {
      nodeId: "chassis",
      role: "chassis_shell",
      label: "Body",
      parentNodeId: null,
      parentAnchor: null,
    },
    {
      nodeId: "head",
      role: "head_shell",
      label: "Head",
      parentNodeId: "chassis",
      parentAnchor: "top",
    },
    {
      nodeId: "beak",
      role: "beak",
      label: "Beak",
      parentNodeId: "head",
      parentAnchor: "face",
    },
  ],
  scenarioIds: ["greet"],
  personalityTraits: ["careful"],
  appearance: {
    primaryColor: "#F4C542",
    secondaryColor: "#FFF2B2",
    accentColor: "#EF7F1A",
    eyeColor: "#111111",
  },
  face: { defaultExpression: "neutral", supportedExpressions: ["neutral", "happy"] },
});

const mesh = (name: string, size: [number, number, number], position: [number, number, number]) => {
  const object = new THREE.Mesh(
    new THREE.BoxGeometry(...size),
    new THREE.MeshBasicMaterial(),
  );
  object.name = name;
  object.position.set(...position);
  return object;
};

describe("Character Robot Studio GLB rig", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("rigs the flat compiler tree and applies wheels, pan, tilt, and face content", () => {
    const model = new THREE.Group();
    model.name = "Character Robot";
    model.rotation.x = -Math.PI / 2;
    const chassis = mesh("chassis", [0.096, 0.076, 0.064], [0, 0, 0.04]);
    const head = mesh("head", [0.078, 0.064, 0.07], [0, 0, 0.118]);
    const beak = mesh("beak", [0.025, 0.02, 0.018], [0, -0.04, 0.113]);
    const wheelLeft = mesh("wheel_left", [0.012, 0.044, 0.044], [-0.053, 0, 0.022]);
    const wheelRight = mesh("wheel_right", [0.012, 0.044, 0.044], [0.053, 0, 0.022]);
    const pan = mesh("neck_pan", [0.022, 0.022, 0.008], [0, 0, 0.077]);
    const tilt = mesh("neck_tilt", [0.03, 0.016, 0.012], [0, 0, 0.087]);
    const display = mesh("hardware_cores3-integrated-display", [0.041, 0.001, 0.03], [0, -0.032, 0.118]);
    model.add(chassis, head, beak, wheelLeft, wheelRight, pan, tilt, display);
    model.updateWorldMatrix(true, true);
    const headBefore = head.getWorldPosition(new THREE.Vector3()).clone();
    const wheelBefore = wheelLeft.getWorldPosition(new THREE.Vector3()).clone();
    const displayBefore = display.getWorldPosition(new THREE.Vector3()).clone();

    const rig = rigStudioModel(model, spec());
    model.updateWorldMatrix(true, true);

    expect(tilt.parent).toBe(pan);
    expect(head.parent).toBe(tilt);
    expect(beak.parent).toBe(head);
    expect(display.parent).toBe(model);
    expect(head.getWorldPosition(new THREE.Vector3())).toEqual(headBefore);
    expect(display.getWorldPosition(new THREE.Vector3())).toEqual(displayBefore);
    expect(wheelLeft.parent?.name).toBe("__wheel_left_pivot");
    expect(wheelLeft.getWorldPosition(new THREE.Vector3())).toEqual(wheelBefore);
    expect(rig.faceDisplay?.mesh.parent).toBe(head);
    const faceBefore = rig.faceDisplay?.mesh.getWorldPosition(new THREE.Vector3()).clone();

    const frame: ScenarioPlaybackFrame = {
      timeS: 0.5,
      progress: 0.5,
      wheels: { leftCommand: 0.5, rightCommand: -0.25 },
      neck: { panDeg: 20, tiltDeg: -8 },
      face: { expression: "happy" },
      soundCue: null,
    };
    applyStudioRigFrame(rig, frame, 0.25);
    model.updateWorldMatrix(true, true);

    expect(pan.rotation.z).toBeCloseTo(THREE.MathUtils.degToRad(20));
    expect(tilt.rotation.x).toBeCloseTo(THREE.MathUtils.degToRad(-8));
    expect(rig.wheelLeft[0].rotation.x).toBeCloseTo(1.5);
    expect(rig.wheelRight[0].rotation.x).toBeCloseTo(-0.75);
    expect(display.getWorldPosition(new THREE.Vector3())).toEqual(displayBefore);
    expect(rig.faceDisplay?.mesh.getWorldPosition(new THREE.Vector3())).not.toEqual(faceBefore);
    expect(rig.faceDisplay?.mesh.material.map?.name).toBe("face:happy");
    expect(rig.faceDisplay?.mesh.userData.face_expression_current).toBe("happy");
    rig.dispose();
  });

  it("creates deterministic expression pixels and reads reduced-motion preference", () => {
    const first = createFaceExpressionTexture("happy", spec().appearance);
    const second = createFaceExpressionTexture("happy", spec().appearance);
    expect(Array.from(first.image.data as Uint8Array)).toEqual(
      Array.from(second.image.data as Uint8Array),
    );
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true }));
    expect(prefersReducedMotion()).toBe(true);
    first.dispose();
    second.dispose();
  });

  it("aggregates transformed compiler primitives after a degenerate first primitive", () => {
    const head = new THREE.Group();
    head.name = "head";
    const degenerate = mesh("head_primitive_0", [0, 0.041, 0.034], [-0.039, 0, 0]);
    const surface = mesh("head_primitive_1", [0.078, 0.064, 0.07], [0.004, -0.002, 0.003]);
    surface.scale.set(0.5, 1, 1);
    const beak = mesh("beak", [0.2, 0.02, 0.02], [0, -0.04, 0]);
    head.add(degenerate, surface, beak);

    const bounds = partLocalBounds(head, new Set(["head", "beak"]));

    expect(bounds).not.toBeNull();
    expect(bounds!.getSize(new THREE.Vector3()).x).toBeCloseTo(0.0625);
    expect(bounds!.getSize(new THREE.Vector3()).z).toBeCloseTo(0.07);
  });

  it("uses the semantic face bezel and places content beyond its front surface", () => {
    const bezelSpec = spec();
    bezelSpec.morphologyNodes.push({
      nodeId: "face_bezel",
      role: "face_bezel",
      label: "Face bezel",
      parentNodeId: "head",
      parentAnchor: "face",
    });
    const model = new THREE.Group();
    const chassis = mesh("chassis", [0.096, 0.076, 0.064], [0, 0, 0.04]);
    const head = new THREE.Group();
    head.name = "head";
    head.position.set(0, 0, 0.118);
    head.add(
      mesh("head_primitive_0", [0, 0.041, 0.034], [-0.039, 0, 0]),
      mesh("head_primitive_1", [0.078, 0.064, 0.07], [0, 0, 0]),
    );
    const beak = mesh("beak", [0.025, 0.02, 0.018], [0, -0.04, 0.113]);
    const bezel = mesh("face_bezel", [0.052, 0.004, 0.038], [0, -0.034, 0.12]);
    model.add(chassis, head, beak, bezel);

    const rig = rigStudioModel(model, bezelSpec);

    expect(rig.faceDisplay?.mesh.parent).toBe(bezel);
    expect(rig.faceDisplay?.mesh.position.y).toBeLessThan(-0.002);
    expect(rig.faceDisplay?.mesh.geometry.parameters.width).toBeCloseTo(0.052 * 0.88);
    rig.dispose();
  });

  it("hides compiler diagnostic keepouts without hiding character parts", () => {
    const model = new THREE.Group();
    const keepout = mesh("keepout_front_access", [1, 1, 1], [0, 0, 0]);
    const character = mesh("head", [1, 1, 1], [0, 0, 0]);
    model.add(keepout, character);

    hideDiagnosticGeometry(model);

    expect(keepout.visible).toBe(false);
    expect(character.visible).toBe(true);
  });
});
