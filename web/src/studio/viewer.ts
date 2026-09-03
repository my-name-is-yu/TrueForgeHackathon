import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

import { sampleScenario } from "./scenario";
import type {
  CharacterAppearanceView,
  CharacterSpecView,
  ScenarioPlaybackFrame,
  ScenarioPreview,
} from "./types";

export type StudioViewer = {
  loadPreview(url: string, spec: CharacterSpecView): Promise<void>;
  clearPreview(): void;
  selectNode(nodeId: string | null): void;
  playScenario(
    scenario: ScenarioPreview,
    onFrame: (frame: ScenarioPlaybackFrame) => void,
    onComplete?: () => void,
  ): void;
  stopScenario(): void;
  destroy(): void;
};

export type StudioViewerOptions = {
  onSelectionChange?: (nodeId: string | null) => void;
  onLoadStateChange?: (state: "loading" | "ready" | "error", message?: string) => void;
};

export type StudioFaceDisplay = {
  mesh: THREE.Mesh<THREE.PlaneGeometry, THREE.MeshBasicMaterial>;
  textures: Map<string, THREE.DataTexture>;
  defaultExpression: string;
};

export type StudioRig = {
  wheelLeft: THREE.Object3D[];
  wheelRight: THREE.Object3D[];
  neckPan: THREE.Object3D[];
  neckTilt: THREE.Object3D[];
  baseRotations: Map<THREE.Object3D, THREE.Euler>;
  faceDisplay: StudioFaceDisplay | null;
  dispose(): void;
};

const EMPTY_RIG = (): StudioRig => ({
  wheelLeft: [],
  wheelRight: [],
  neckPan: [],
  neckTilt: [],
  baseRotations: new Map(),
  faceDisplay: null,
  dispose() {},
});

const colorBytes = (value: string, fallback: string): [number, number, number] => {
  const color = new THREE.Color();
  try {
    color.set(value);
  } catch {
    color.set(fallback);
  }
  return [
    Math.round(THREE.MathUtils.clamp(color.r, 0, 1) * 255),
    Math.round(THREE.MathUtils.clamp(color.g, 0, 1) * 255),
    Math.round(THREE.MathUtils.clamp(color.b, 0, 1) * 255),
  ];
};

const expressionSeed = (expression: string): number => (
  [...expression].reduce((seed, character) => ((seed * 33) ^ character.codePointAt(0)!) >>> 0, 5381)
);

export function createFaceExpressionTexture(
  expression: string,
  appearance: CharacterAppearanceView,
): THREE.DataTexture {
  const width = 64;
  const height = 48;
  const background = colorBytes(appearance.secondaryColor, "#F7E9AF");
  const ink = colorBytes(appearance.eyeColor, "#111111");
  const accent = colorBytes(appearance.accentColor, "#EF7F1A");
  const data = new Uint8Array(width * height * 4);
  for (let offset = 0; offset < data.length; offset += 4) {
    data[offset] = background[0];
    data[offset + 1] = background[1];
    data[offset + 2] = background[2];
    data[offset + 3] = 255;
  }
  const pixel = (x: number, y: number, color: [number, number, number]): void => {
    if (x < 0 || x >= width || y < 0 || y >= height) return;
    const offset = ((Math.floor(y) * width) + Math.floor(x)) * 4;
    data[offset] = color[0];
    data[offset + 1] = color[1];
    data[offset + 2] = color[2];
  };
  const rectangle = (
    x: number,
    y: number,
    rectangleWidth: number,
    rectangleHeight: number,
    color: [number, number, number],
  ): void => {
    for (let row = y; row < y + rectangleHeight; row += 1) {
      for (let column = x; column < x + rectangleWidth; column += 1) pixel(column, row, color);
    }
  };
  const line = (
    fromX: number,
    fromY: number,
    toX: number,
    toY: number,
    color: [number, number, number],
  ): void => {
    const steps = Math.max(Math.abs(toX - fromX), Math.abs(toY - fromY), 1);
    for (let step = 0; step <= steps; step += 1) {
      pixel(
        Math.round(fromX + ((toX - fromX) * step) / steps),
        Math.round(fromY + ((toY - fromY) * step) / steps),
        color,
      );
    }
  };

  const normalized = expression.toLowerCase();
  const sleepy = normalized.includes("sleep");
  const delighted = normalized.includes("happy") || normalized.includes("delight");
  const listening = normalized.includes("listen");
  const thinking = normalized.includes("think");
  const seed = expressionSeed(expression);
  const gazeOffset = thinking ? 3 : listening ? -2 : (seed % 3) - 1;
  if (sleepy) {
    rectangle(13, 18, 13, 2, ink);
    rectangle(38, 18, 13, 2, ink);
  } else if (delighted) {
    line(13, 20, 19, 15, ink);
    line(19, 15, 25, 20, ink);
    line(39, 20, 45, 15, ink);
    line(45, 15, 51, 20, ink);
  } else {
    rectangle(16 + gazeOffset, 14, listening ? 7 : 5, listening ? 9 : 7, ink);
    rectangle(41 + gazeOffset, 14, listening ? 7 : 5, listening ? 9 : 7, ink);
  }
  rectangle(8, 28, 6, 3, accent);
  rectangle(50, 28, 6, 3, accent);
  if (delighted) {
    line(23, 32, 28, 36, ink);
    line(28, 36, 36, 36, ink);
    line(36, 36, 41, 32, ink);
  } else if (sleepy) {
    rectangle(29, 34, 6, 2, ink);
  } else if (thinking) {
    line(27, 34, 36, 32, ink);
  } else {
    rectangle(26, 33, 12, 2, ink);
  }

  const texture = new THREE.DataTexture(data, width, height, THREE.RGBAFormat);
  texture.name = `face:${expression}`;
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.magFilter = THREE.NearestFilter;
  texture.minFilter = THREE.NearestFilter;
  texture.generateMipmaps = false;
  texture.needsUpdate = true;
  return texture;
}

const disposeObject = (object: THREE.Object3D): void => {
  object.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) return;
    child.geometry.dispose();
    const materials = Array.isArray(child.material) ? child.material : [child.material];
    materials.forEach((material) => material.dispose());
  });
};

const safePreviewUrl = (value: string): string => {
  const url = new URL(value, window.location.href);
  if ((url.protocol !== "http:" && url.protocol !== "https:") || url.origin !== window.location.origin) {
    throw new Error("Preview GLB must use a same-origin HTTP URL");
  }
  return `${url.pathname}${url.search}`;
};

const semanticNodeId = (
  object: THREE.Object3D,
  model: THREE.Object3D,
  partNames: ReadonlySet<string>,
): string | null => {
  let candidate: THREE.Object3D | null = object;
  while (candidate && candidate !== model.parent) {
    const metadataId = candidate.userData.node_id;
    if (typeof metadataId === "string" && metadataId.length > 0) return metadataId;
    if (candidate.name && partNames.has(candidate.name)) return candidate.name;
    if (candidate === model) break;
    candidate = candidate.parent;
  }
  return null;
};

const objectBounds = (object: THREE.Object3D): THREE.Box3 | null => {
  let result: THREE.Box3 | null = null;
  object.traverse((candidate) => {
    if (result || !(candidate instanceof THREE.Mesh)) return;
    candidate.geometry.computeBoundingBox();
    if (candidate.geometry.boundingBox) result = candidate.geometry.boundingBox.clone();
  });
  return result;
};

const attach = (parent: THREE.Object3D, child: THREE.Object3D): void => {
  if (parent === child || child.getObjectById(parent.id)) return;
  parent.attach(child);
};

const createWheelPivot = (
  wheel: THREE.Object3D | undefined,
  role: "wheel_left" | "wheel_right",
): THREE.Object3D | null => {
  if (!wheel?.parent) return null;
  const parent = wheel.parent;
  const pivot = new THREE.Group();
  pivot.name = `__${role}_pivot`;
  pivot.userData.motion_role = role;
  pivot.position.copy(wheel.position);
  parent.add(pivot);
  pivot.updateWorldMatrix(true, false);
  attach(pivot, wheel);
  wheel.userData.crs_motion_child = true;
  return pivot;
};

const createFaceDisplay = (
  anchor: THREE.Object3D,
  spec: CharacterSpecView,
): StudioFaceDisplay | null => {
  const bounds = objectBounds(anchor);
  if (!bounds) return null;
  const size = bounds.getSize(new THREE.Vector3());
  if (size.x <= 0 || size.z <= 0) return null;
  const expressions = [...new Set([
    spec.face.defaultExpression,
    ...spec.face.supportedExpressions,
  ])];
  const textures = new Map(expressions.map((expression) => [
    expression,
    createFaceExpressionTexture(expression, spec.appearance),
  ]));
  const material = new THREE.MeshBasicMaterial({
    map: textures.get(spec.face.defaultExpression),
    side: THREE.DoubleSide,
    toneMapped: false,
  });
  const geometry = new THREE.PlaneGeometry(size.x * 0.88, size.z * 0.82);
  const mesh = new THREE.Mesh(geometry, material);
  mesh.name = "__face_display_content";
  mesh.userData.face_expression_current = spec.face.defaultExpression;
  mesh.position.set(
    (bounds.min.x + bounds.max.x) / 2,
    bounds.min.y - Math.max(size.y * 0.03, 0.0004),
    (bounds.min.z + bounds.max.z) / 2,
  );
  mesh.rotation.x = Math.PI / 2;
  mesh.renderOrder = 4;
  anchor.add(mesh);
  return {
    mesh,
    textures,
    defaultExpression: spec.face.defaultExpression,
  };
};

export function rigStudioModel(model: THREE.Object3D, spec: CharacterSpecView): StudioRig {
  const byName = new Map<string, THREE.Object3D>();
  model.traverse((object) => {
    if (object.name && !byName.has(object.name)) byName.set(object.name, object);
  });

  for (const node of spec.morphologyNodes) {
    if (!node.parentNodeId) continue;
    const child = byName.get(node.nodeId);
    const parent = byName.get(node.parentNodeId);
    if (child && parent) attach(parent, child);
  }

  const headNode = spec.morphologyNodes.find((node) => node.role === "head_shell");
  const head = headNode ? byName.get(headNode.nodeId) : undefined;
  const pan = byName.get("neck_pan") ?? byName.get("head_pan");
  const tilt = byName.get("neck_tilt") ?? byName.get("head_tilt");
  if (pan && tilt) attach(pan, tilt);
  if (head && tilt) attach(tilt, head);
  else if (head && pan) attach(pan, head);

  const hardwareDisplay = [...byName.values()].find((object) => {
    const name = object.name.toLowerCase();
    return name.startsWith("hardware_") && (name.includes("display") || name.includes("lcd"));
  });
  const faceAnchor = head ?? hardwareDisplay;
  const faceDisplay = faceAnchor ? createFaceDisplay(faceAnchor, spec) : null;

  const wheelLeft = createWheelPivot(
    byName.get("wheel_left") ?? byName.get("left_wheel"),
    "wheel_left",
  );
  const wheelRight = createWheelPivot(
    byName.get("wheel_right") ?? byName.get("right_wheel"),
    "wheel_right",
  );
  const moving = [wheelLeft, wheelRight, pan, tilt].filter(
    (object): object is THREE.Object3D => object !== null && object !== undefined,
  );
  const baseRotations = new Map(moving.map((object) => [object, object.rotation.clone()]));

  return {
    wheelLeft: wheelLeft ? [wheelLeft] : [],
    wheelRight: wheelRight ? [wheelRight] : [],
    neckPan: pan ? [pan] : [],
    neckTilt: tilt ? [tilt] : [],
    baseRotations,
    faceDisplay,
    dispose() {
      faceDisplay?.textures.forEach((texture) => texture.dispose());
    },
  };
}

export function applyStudioRigFrame(
  rig: StudioRig,
  frame: ScenarioPlaybackFrame,
  deltaS: number,
): void {
  rig.neckPan.forEach((object) => {
    const base = rig.baseRotations.get(object)!;
    // build123d exports a Z-up assembly beneath a root-axis conversion.
    object.rotation.z = base.z + THREE.MathUtils.degToRad(frame.neck.panDeg);
  });
  rig.neckTilt.forEach((object) => {
    const base = rig.baseRotations.get(object)!;
    object.rotation.x = base.x + THREE.MathUtils.degToRad(frame.neck.tiltDeg);
  });
  rig.wheelLeft.forEach((object) => {
    object.rotation.x += frame.wheels.leftCommand * deltaS * 12;
  });
  rig.wheelRight.forEach((object) => {
    object.rotation.x += frame.wheels.rightCommand * deltaS * 12;
  });
  if (rig.faceDisplay) {
    const texture = rig.faceDisplay.textures.get(frame.face.expression)
      ?? rig.faceDisplay.textures.get(rig.faceDisplay.defaultExpression);
    if (texture) rig.faceDisplay.mesh.material.map = texture;
    rig.faceDisplay.mesh.material.needsUpdate = true;
    rig.faceDisplay.mesh.userData.face_expression_current = frame.face.expression;
  }
}

export const prefersReducedMotion = (): boolean => (
  typeof window.matchMedia === "function"
  && window.matchMedia("(prefers-reduced-motion: reduce)").matches
);

const frameCamera = (
  model: THREE.Object3D,
  camera: THREE.PerspectiveCamera,
  controls: OrbitControls,
): void => {
  const box = new THREE.Box3().setFromObject(model);
  if (box.isEmpty()) return;
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(size.length() * 0.55, 0.1);
  camera.near = Math.max(radius / 100, 0.001);
  camera.far = Math.max(radius * 100, 10);
  camera.position.copy(center).add(new THREE.Vector3(radius * 1.25, radius * 0.85, radius * 1.5));
  camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.minDistance = radius * 0.3;
  controls.maxDistance = radius * 12;
  controls.update();
};

export function createStudioViewer(
  container: HTMLElement,
  options: StudioViewerOptions = {},
): StudioViewer {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x111619);
  scene.fog = new THREE.Fog(0x111619, 3, 12);

  const camera = new THREE.PerspectiveCamera(38, 1, 0.01, 100);
  camera.position.set(1.4, 1, 1.8);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.domElement.setAttribute("role", "img");
  renderer.domElement.setAttribute("aria-label", "Interactive 3D character robot preview");
  renderer.domElement.tabIndex = 0;
  container.appendChild(renderer.domElement);

  scene.add(new THREE.HemisphereLight(0xf3f0e7, 0x1c292b, 2.6));
  const keyLight = new THREE.DirectionalLight(0xffdfaa, 5.5);
  keyLight.position.set(3, 4, 5);
  scene.add(keyLight);
  const rimLight = new THREE.DirectionalLight(0x75d5d1, 3.2);
  rimLight.position.set(-4, 2, -3);
  scene.add(rimLight);

  const grid = new THREE.GridHelper(5, 40, 0x52706e, 0x263437);
  grid.material.transparent = true;
  grid.material.opacity = 0.4;
  scene.add(grid);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;

  const loader = new GLTFLoader();
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  let model: THREE.Object3D | null = null;
  let partNames = new Set<string>();
  let selectedNodeId: string | null = null;
  let selectionHelper: THREE.BoxHelper | null = null;
  let rig = EMPTY_RIG();
  let playbackHandle: number | null = null;
  let playbackGeneration = 0;
  let renderHandle: number | null = null;
  let loadSequence = 0;
  let destroyed = false;
  let pointerStart: { x: number; y: number } | null = null;

  const clearSelectionHelper = (): void => {
    if (!selectionHelper) return;
    scene.remove(selectionHelper);
    selectionHelper.geometry.dispose();
    selectionHelper.material.dispose();
    selectionHelper = null;
  };

  const objectForNode = (nodeId: string): THREE.Object3D | null => {
    if (!model) return null;
    let found: THREE.Object3D | null = null;
    model.traverse((object) => {
      if (found) return;
      if (object.userData.node_id === nodeId || object.name === nodeId) found = object;
    });
    return found;
  };

  const selectNode = (nodeId: string | null): void => {
    clearSelectionHelper();
    selectedNodeId = nodeId;
    if (nodeId) {
      const selected = objectForNode(nodeId);
      if (selected) {
        selectionHelper = new THREE.BoxHelper(selected, 0xffc857);
        selectionHelper.material.depthTest = false;
        selectionHelper.renderOrder = 5;
        scene.add(selectionHelper);
      }
    }
  };

  const stopScenario = (): void => {
    playbackGeneration += 1;
    if (playbackHandle !== null) window.cancelAnimationFrame(playbackHandle);
    playbackHandle = null;
  };

  const clearModel = (): void => {
    stopScenario();
    clearSelectionHelper();
    rig.dispose();
    if (model) {
      scene.remove(model);
      disposeObject(model);
    }
    model = null;
    rig = EMPTY_RIG();
  };

  const resize = (): void => {
    const width = Math.max(container.clientWidth, 1);
    const height = Math.max(container.clientHeight, 1);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  };

  const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resize);
  resizeObserver?.observe(container);
  window.addEventListener("resize", resize);
  resize();

  const render = (): void => {
    if (destroyed) return;
    controls.update();
    selectionHelper?.update();
    renderer.render(scene, camera);
    renderHandle = window.requestAnimationFrame(render);
  };
  renderHandle = window.requestAnimationFrame(render);

  const onPointerDown = (event: PointerEvent): void => {
    pointerStart = { x: event.clientX, y: event.clientY };
  };

  const onPointerUp = (event: PointerEvent): void => {
    if (!model || !pointerStart) return;
    const movement = Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y);
    pointerStart = null;
    if (movement > 5) return;
    const rect = renderer.domElement.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -(((event.clientY - rect.top) / rect.height) * 2 - 1);
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObject(model, true)[0];
    const nodeId = hit ? semanticNodeId(hit.object, model, partNames) : null;
    selectNode(nodeId);
    options.onSelectionChange?.(nodeId);
  };

  renderer.domElement.addEventListener("pointerdown", onPointerDown);
  renderer.domElement.addEventListener("pointerup", onPointerUp);

  return {
    async loadPreview(url, spec) {
      const sequence = ++loadSequence;
      const resolvedUrl = safePreviewUrl(url);
      stopScenario();
      options.onLoadStateChange?.("loading");
      await new Promise<void>((resolve, reject) => {
        loader.load(
          resolvedUrl,
          (gltf) => {
            if (destroyed || sequence !== loadSequence) {
              disposeObject(gltf.scene);
              resolve();
              return;
            }
            clearModel();
            model = gltf.scene;
            partNames = new Set(spec.morphologyNodes.map((node) => node.nodeId));
            rig = rigStudioModel(model, spec);
            model.traverse((object) => {
              if (object instanceof THREE.Mesh) {
                const isDisplayContent = object.name === "__face_display_content";
                object.castShadow = !isDisplayContent;
                object.receiveShadow = !isDisplayContent;
              }
            });
            scene.add(model);
            frameCamera(model, camera, controls);
            selectNode(selectedNodeId);
            options.onLoadStateChange?.("ready");
            resolve();
          },
          undefined,
          (error) => {
            if (destroyed || sequence !== loadSequence) {
              resolve();
              return;
            }
            const message = error instanceof Error ? error.message : "GLB preview could not be loaded";
            options.onLoadStateChange?.("error", message);
            reject(new Error(message));
          },
        );
      });
    },
    clearPreview() {
      loadSequence += 1;
      clearModel();
      options.onLoadStateChange?.("ready");
    },
    selectNode,
    playScenario(scenario, onFrame, onComplete) {
      stopScenario();
      const generation = playbackGeneration;
      if (prefersReducedMotion()) {
        const frame = sampleScenario(scenario, scenario.durationS);
        applyStudioRigFrame(rig, frame, 0);
        onFrame(frame);
        onComplete?.();
        return;
      }
      const start = performance.now();
      let previous = start;
      const tick = (now: number): void => {
        if (generation !== playbackGeneration || destroyed) return;
        const elapsedS = Math.min((now - start) / 1000, scenario.durationS);
        const frame = sampleScenario(scenario, elapsedS);
        applyStudioRigFrame(rig, frame, Math.max(0, (now - previous) / 1000));
        previous = now;
        onFrame(frame);
        if (elapsedS >= scenario.durationS) {
          playbackHandle = null;
          onComplete?.();
          return;
        }
        playbackHandle = window.requestAnimationFrame(tick);
      };
      playbackHandle = window.requestAnimationFrame(tick);
    },
    stopScenario,
    destroy() {
      destroyed = true;
      loadSequence += 1;
      stopScenario();
      if (renderHandle !== null) window.cancelAnimationFrame(renderHandle);
      resizeObserver?.disconnect();
      window.removeEventListener("resize", resize);
      renderer.domElement.removeEventListener("pointerdown", onPointerDown);
      renderer.domElement.removeEventListener("pointerup", onPointerUp);
      controls.dispose();
      clearModel();
      grid.geometry.dispose();
      grid.material.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
