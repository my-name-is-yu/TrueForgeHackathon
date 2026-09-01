import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const lengths = [0.4, 0.35, 0.25];

export type Preview = { joint: string; attribute: string } | null;

export function createRobotView(container: HTMLElement): { update(preview: Preview): void } {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x09110f);
  scene.fog = new THREE.Fog(0x09110f, 2.2, 4.5);
  const camera = new THREE.PerspectiveCamera(38, 1, 0.01, 20);
  camera.position.set(1.45, 1.2, 1.15);
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  container.appendChild(renderer.domElement);

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
  const joints: THREE.Mesh[] = [];
  let parent: THREE.Group = robot;
  const angles = [0.35, -0.45, 0.25];
  lengths.forEach((length, index) => {
    const pivot = new THREE.Group();
    pivot.rotation.z = angles[index];
    parent.add(pivot);
    const joint = new THREE.Mesh(
      new THREE.CylinderGeometry(0.065, 0.065, 0.12, 28),
      new THREE.MeshStandardMaterial({ color: 0xf5c56d, metalness: 0.35, roughness: 0.3 }),
    );
    joint.rotation.x = Math.PI / 2;
    pivot.add(joint);
    joints.push(joint);
    const link = new THREE.Mesh(
      new THREE.CapsuleGeometry(0.038, length - 0.076, 10, 24),
      new THREE.MeshStandardMaterial({ color: 0x78d6bd, metalness: 0.22, roughness: 0.32 }),
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
    new THREE.MeshStandardMaterial({ color: 0xff755c, emissive: 0x4a0903, roughness: 0.24 }),
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
    update(preview) {
      joints.forEach((joint, index) => {
        const material = joint.material as THREE.MeshStandardMaterial;
        material.color.set(preview?.joint === `joint_${String.fromCharCode(97 + index)}` ? 0xff755c : 0xf5c56d);
        material.emissive.set(preview?.joint === `joint_${String.fromCharCode(97 + index)}` ? 0x3c0802 : 0x000000);
      });
    },
  };
}
