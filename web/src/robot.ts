import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const lengths = [0.4, 0.35, 0.25];
const jointNames = ["joint_a", "joint_b", "joint_c"];
const palette = {
  background: 0x09110f,
  baseAxis: 0x78d6bd,
  draft: 0xff755c,
  joint: 0xf5c56d,
  locked: 0x71877f,
};

export type RobotAxis = [number, number, number];
export type RobotScalarAttribute = "damping" | "armature" | "frictionloss";

export type RobotJoint = {
  name: string;
  axis: RobotAxis;
  damping: number;
  armature: number;
  frictionloss: number;
};

export type RobotPatch =
  | {
    target: { name: string };
    attribute: "axis";
    new_value: RobotAxis;
  }
  | {
    target: { name: string };
    attribute: RobotScalarAttribute;
    new_value: number;
  };

export type RobotDraft = { patch: RobotPatch };

export type RobotViewState = {
  revisionId: string;
  joints: readonly RobotJoint[];
  selectedJointName?: string | null;
  draft?: RobotDraft | null;
  editingLocked: boolean;
  qualificationState: "unused" | "running" | "passed" | "failed";
};

export type RobotScalarPreview = {
  attribute: RobotScalarAttribute;
  before: number;
  after: number;
};

export type RobotJointViewModel = {
  name: string;
  base: RobotJoint;
  selected: boolean;
  draft: { attribute: RobotPatch["attribute"]; newValue: number | RobotAxis } | null;
  baseAxis: RobotAxis;
  draftAxis: RobotAxis | null;
  displayAxis: RobotAxis;
  scalarPreview: RobotScalarPreview | null;
};

export type RobotViewModel = {
  revisionId: string;
  editingLocked: boolean;
  joints: RobotJointViewModel[];
  readout: string | null;
};

const copyAxis = (axis: RobotAxis): RobotAxis => [...axis];
const formatNumber = (value: number): string => String(value);
const formatAxis = (axis: RobotAxis): string => `[${axis.map(formatNumber).join(", ")}]`;

export function buildRobotViewModel(state: RobotViewState): RobotViewModel {
  const joints = state.joints.map((joint): RobotJointViewModel => {
    const base: RobotJoint = { ...joint, axis: copyAxis(joint.axis) };
    const patch = state.draft?.patch.target.name === joint.name
      ? state.draft.patch
      : null;
    const baseAxis = copyAxis(joint.axis);
    const draftAxis = patch?.attribute === "axis" ? copyAxis(patch.new_value) : null;
    const scalarPreview = patch && patch.attribute !== "axis"
      ? {
        attribute: patch.attribute,
        before: joint[patch.attribute],
        after: patch.new_value,
      }
      : null;

    return {
      name: joint.name,
      base,
      selected: state.selectedJointName === joint.name,
      draft: patch
        ? {
          attribute: patch.attribute,
          newValue: Array.isArray(patch.new_value) ? copyAxis(patch.new_value) : patch.new_value,
        }
        : null,
      baseAxis,
      draftAxis,
      displayAxis: draftAxis ? copyAxis(draftAxis) : copyAxis(baseAxis),
      scalarPreview,
    };
  });

  const changedJoint = joints.find((joint) => joint.draft !== null);
  const selectedJoint = joints.find((joint) => joint.selected);
  let readout: string | null = null;
  if (changedJoint?.scalarPreview) {
    const preview = changedJoint.scalarPreview;
    readout = `${changedJoint.name} ${preview.attribute} ${formatNumber(preview.before)} → ${formatNumber(preview.after)}`;
  } else if (changedJoint?.draftAxis) {
    readout = `${changedJoint.name} axis ${formatAxis(changedJoint.baseAxis)} → ${formatAxis(changedJoint.draftAxis)}`;
  } else if (selectedJoint) {
    readout = `${selectedJoint.name} selected · damping ${formatNumber(selectedJoint.base.damping)} · armature ${formatNumber(selectedJoint.base.armature)} · frictionloss ${formatNumber(selectedJoint.base.frictionloss)}`;
  }
  if (state.qualificationState === "failed") {
    readout = readout
      ? `Qualification failed — reset required · ${readout}`
      : "Qualification failed — reset required";
  } else if (state.editingLocked) {
    readout = readout
      ? `Qualified — editing locked · ${readout}`
      : "Qualified — editing locked";
  }

  return {
    revisionId: state.revisionId,
    editingLocked: state.editingLocked,
    joints,
    readout,
  };
}

type JointVisual = {
  mesh: THREE.Mesh<THREE.CylinderGeometry, THREE.MeshStandardMaterial>;
  baseAxis: THREE.ArrowHelper;
  draftAxis: THREE.ArrowHelper;
};

const direction = (axis: RobotAxis): THREE.Vector3 => {
  const vector = new THREE.Vector3(...axis);
  return vector.lengthSq() === 0 ? vector.set(0, 0, 1) : vector.normalize();
};

export function createRobotView(container: HTMLElement): { update(state: RobotViewState): void } {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(palette.background);
  scene.fog = new THREE.Fog(palette.background, 2.2, 4.5);
  const camera = new THREE.PerspectiveCamera(38, 1, 0.01, 20);
  camera.position.set(1.45, 1.2, 1.15);
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.domElement.setAttribute("role", "img");
  renderer.domElement.setAttribute("aria-label", "3D robot design");
  container.appendChild(renderer.domElement);

  if (!container.style.position) container.style.position = "relative";
  const readout = document.createElement("div");
  readout.className = "robot-readout";
  readout.setAttribute("role", "status");
  readout.setAttribute("aria-live", "polite");
  Object.assign(readout.style, {
    position: "absolute",
    left: "14px",
    bottom: "14px",
    zIndex: "1",
    maxWidth: "calc(100% - 28px)",
    padding: "8px 10px",
    border: "1px solid #34564b",
    borderRadius: "7px",
    background: "rgba(9, 21, 17, .92)",
    color: "#c2d9d0",
    font: "10px ui-monospace, SFMono-Regular, monospace",
    pointerEvents: "none",
  });
  readout.hidden = true;
  container.appendChild(readout);

  scene.add(new THREE.HemisphereLight(0xe6fff6, 0x15201d, 2.3));
  const key = new THREE.DirectionalLight(0xffd99d, 4);
  key.position.set(2, 2, 3);
  scene.add(key);
  const grid = new THREE.GridHelper(3, 24, 0x315149, 0x172a25);
  grid.rotation.x = Math.PI / 2;
  grid.position.z = -0.12;
  scene.add(grid);

  const robot = new THREE.Group();
  robot.rotation.x = -0.12;
  scene.add(robot);
  const joints: JointVisual[] = [];
  let parent: THREE.Group = robot;
  const angles = [0.35, -0.45, 0.25];
  lengths.forEach((length, index) => {
    const pivot = new THREE.Group();
    pivot.rotation.z = angles[index];
    parent.add(pivot);
    const joint = new THREE.Mesh(
      new THREE.CylinderGeometry(0.065, 0.065, 0.12, 28),
      new THREE.MeshStandardMaterial({ color: palette.joint, metalness: 0.35, roughness: 0.3 }),
    );
    joint.rotation.x = Math.PI / 2;
    pivot.add(joint);
    const baseAxis = new THREE.ArrowHelper(direction([0, 0, 1]), new THREE.Vector3(), 0.2, palette.baseAxis, 0.055, 0.035);
    const draftAxis = new THREE.ArrowHelper(direction([0, 0, 1]), new THREE.Vector3(), 0.24, palette.draft, 0.065, 0.04);
    draftAxis.visible = false;
    pivot.add(baseAxis, draftAxis);
    joints.push({ mesh: joint, baseAxis, draftAxis });
    const link = new THREE.Mesh(
      new THREE.CapsuleGeometry(0.038, length - 0.076, 10, 24),
      new THREE.MeshStandardMaterial({ color: palette.baseAxis, metalness: 0.22, roughness: 0.32 }),
    );
    link.rotation.z = -Math.PI / 2;
    link.position.x = length / 2;
    pivot.add(link);
    const next = new THREE.Group();
    next.position.x = length;
    pivot.add(next);
    parent = next;
  });
  const effector = new THREE.Mesh(
    new THREE.SphereGeometry(0.065, 28, 18),
    new THREE.MeshStandardMaterial({ color: palette.draft, emissive: 0x4a0903, roughness: 0.24 }),
  );
  parent.add(effector);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.target.set(0.4, 0.05, 0);
  controls.minDistance = 0.7;
  controls.maxDistance = 4;

  const resize = () => {
    const width = container.clientWidth;
    const height = container.clientHeight;
    if (renderer.domElement.width !== width || renderer.domElement.height !== height) {
      renderer.setSize(width, height, false);
      camera.aspect = width / Math.max(height, 1);
      camera.updateProjectionMatrix();
    }
  };
  renderer.setAnimationLoop(() => {
    resize();
    controls.update();
    renderer.render(scene, camera);
  });

  return {
    update(state) {
      const model = buildRobotViewModel(state);
      container.dataset.revisionId = model.revisionId;
      container.dataset.editingLocked = String(model.editingLocked);
      renderer.domElement.setAttribute(
        "aria-label",
        `3D robot design at ${model.revisionId}${state.qualificationState === "failed"
          ? ", qualification failed and reset required"
          : model.editingLocked ? ", qualified and editing locked" : ""}`,
      );
      joints.forEach((visual, index) => {
        const joint = model.joints.find((item) => item.name === jointNames[index]);
        if (!joint) {
          visual.baseAxis.visible = false;
          visual.draftAxis.visible = false;
          return;
        }
        visual.baseAxis.visible = true;
        visual.baseAxis.setDirection(direction(joint.baseAxis));
        visual.draftAxis.visible = joint.draftAxis !== null;
        if (joint.draftAxis) visual.draftAxis.setDirection(direction(joint.displayAxis));

        const active = joint.selected || joint.draft !== null;
        visual.mesh.material.color.set(
          active ? palette.draft : model.editingLocked ? palette.locked : palette.joint,
        );
        visual.mesh.material.emissive.set(active ? 0x3c0802 : 0x000000);
      });
      readout.textContent = model.readout ?? "";
      readout.hidden = model.readout === null;
      readout.style.borderColor = model.editingLocked ? "#6e532e" : "#34564b";
      readout.style.color = model.editingLocked ? "#ffc67d" : "#c2d9d0";
    },
  };
}
