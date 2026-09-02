import { describe, expect, it } from "vitest";

import {
  buildRobotViewModel,
  type RobotJoint,
  type RobotViewState,
} from "../src/robot";

const baseJoints = (): RobotJoint[] => [
  {
    name: "joint_a",
    axis: [0, 0, 1],
    damping: 0.4,
    armature: 0.02,
    frictionloss: 0,
  },
  {
    name: "joint_b",
    axis: [0, 0, 1],
    damping: 0.4,
    armature: 0.02,
    frictionloss: 0,
  },
  {
    name: "joint_c",
    axis: [0, 1, 0],
    damping: 0.01,
    armature: 0.02,
    frictionloss: 0,
  },
];

const state = (overrides: Partial<RobotViewState> = {}): RobotViewState => ({
  revisionId: "r000",
  joints: baseJoints(),
  selectedJointName: "joint_a",
  draft: null,
  editingLocked: false,
  qualificationState: "unused",
  ...overrides,
});

describe("buildRobotViewModel", () => {
  it("derives selected base values and authored axes without a draft", () => {
    const model = buildRobotViewModel(state());

    expect(model.revisionId).toBe("r000");
    expect(model.editingLocked).toBe(false);
    expect(model.joints[0]).toMatchObject({
      name: "joint_a",
      selected: true,
      draft: null,
      baseAxis: [0, 0, 1],
      draftAxis: null,
      displayAxis: [0, 0, 1],
      scalarPreview: null,
    });
    expect(model.joints[2].displayAxis).toEqual([0, 1, 0]);
    expect(model.readout).toBe("joint_a selected · damping 0.4 · armature 0.02 · frictionloss 0");
  });

  it("keeps the base axis and exposes a distinct draft direction", () => {
    const model = buildRobotViewModel(state({
      draft: {
        patch: {
          target: { name: "joint_b" },
          attribute: "axis",
          new_value: [0, 1, 0],
        },
      },
    }));
    const joint = model.joints[1];

    expect(joint.base.axis).toEqual([0, 0, 1]);
    expect(joint.baseAxis).toEqual([0, 0, 1]);
    expect(joint.draft).toEqual({ attribute: "axis", newValue: [0, 1, 0] });
    expect(joint.draftAxis).toEqual([0, 1, 0]);
    expect(joint.displayAxis).toEqual([0, 1, 0]);
    expect(joint.scalarPreview).toBeNull();
    expect(model.readout).toBe("joint_b axis [0, 0, 1] → [0, 1, 0]");
  });

  it("reports a scalar before and after without changing display geometry", () => {
    const joints = baseJoints();
    const model = buildRobotViewModel(state({
      joints,
      draft: {
        patch: {
          target: { name: "joint_b" },
          attribute: "damping",
          new_value: 0.7,
        },
      },
    }));
    const joint = model.joints[1];

    expect(joint.displayAxis).toEqual(joint.baseAxis);
    expect(joint.draftAxis).toBeNull();
    expect(joint.scalarPreview).toEqual({ attribute: "damping", before: 0.4, after: 0.7 });
    expect(model.readout).toBe("joint_b damping 0.4 → 0.7");
    expect(joints[1].damping).toBe(0.4);
  });

  it("uses committed values as the new base and clears draft presentation", () => {
    const joints = baseJoints();
    joints[1] = { ...joints[1], damping: 0.7 };
    const model = buildRobotViewModel(state({
      revisionId: "r001",
      joints,
      selectedJointName: "joint_b",
      editingLocked: true,
      qualificationState: "passed",
    }));
    const joint = model.joints[1];

    expect(joint.base.damping).toBe(0.7);
    expect(joint.draft).toBeNull();
    expect(joint.scalarPreview).toBeNull();
    expect(model.readout).toBe("Qualified — editing locked · joint_b selected · damping 0.7 · armature 0.02 · frictionloss 0");
  });

  it("makes a terminal qualification failure explicit and points to Reset", () => {
    const model = buildRobotViewModel(state({
      editingLocked: true,
      qualificationState: "failed",
    }));

    expect(model.editingLocked).toBe(true);
    expect(model.readout).toBe(
      "Qualification failed — reset required · joint_a selected · damping 0.4 · armature 0.02 · frictionloss 0",
    );
  });

  it("returns an editable reset-like r000 model without prior draft state", () => {
    const reset = buildRobotViewModel(state({
      revisionId: "r000",
      selectedJointName: null,
      editingLocked: false,
    }));

    expect(reset).toMatchObject({
      revisionId: "r000",
      editingLocked: false,
      readout: null,
    });
    expect(reset.joints.every((joint) => joint.draft === null)).toBe(true);
    expect(reset.joints.map((joint) => joint.base.axis)).toEqual([
      [0, 0, 1],
      [0, 0, 1],
      [0, 1, 0],
    ]);
  });
});
