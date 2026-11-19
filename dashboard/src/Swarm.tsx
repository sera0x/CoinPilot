import { useEffect, useRef } from "react";
import * as THREE from "three";

type Role = { name: string; count: number; radius: number; angle: number; color: string };

/** One glowing node per agent. Scouts get three nodes (they run in parallel). */
const ROLES: Role[] = [
  { name: "instructor", count: 90, radius: 0.9, angle: 0.0, color: "#e6edf3" },
  { name: "scout", count: 130, radius: 3.1, angle: 0.4, color: "#3fb950" },
  { name: "scout", count: 130, radius: 3.1, angle: 2.5, color: "#3fb950" },
  { name: "scout", count: 130, radius: 3.1, angle: 4.6, color: "#3fb950" },
  { name: "filter", count: 170, radius: 2.3, angle: 1.4, color: "#58a6ff" },
  { name: "analyst", count: 150, radius: 1.8, angle: 3.6, color: "#bc8cff" },
  { name: "risk", count: 110, radius: 2.6, angle: 5.4, color: "#f0883e" },
  { name: "trader", count: 130, radius: 1.4, angle: 5.9, color: "#d29922" },
];

const BAD: Record<string, string> = {
  "#3fb950": "#f85149",
  "#58a6ff": "#f85149",
  "#bc8cff": "#f0883e",
  "#f0883e": "#f85149",
  "#d29922": "#f0883e",
  "#e6edf3": "#ffa198",
};

function badColor(hex: string): string {
  return BAD[hex] ?? hex;
}

export default function Swarm({ activity, healthy }: { activity: number; healthy: boolean }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const stateRef = useRef({ activity, healthy });

  useEffect(() => {
    stateRef.current = { activity, healthy };
  }, [activity, healthy]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(55, 1, 0.1, 100);
    camera.position.set(0, 1.4, 6.4);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    host.appendChild(renderer.domElement);

    const nodeCount = ROLES.length;
    const nodePos = ROLES.map(() => new THREE.Vector3());
    const total = ROLES.reduce((sum, r) => sum + r.count, 0);

    const positions = new Float32Array(total * 3);
    const colors = new Float32Array(total * 3);
    const target = new Float32Array(total * 3);
    const sizes = new Float32Array(total);
    const fromIdx = new Int16Array(total);
    const toIdx = new Int16Array(total);
    const tParam = new Float32Array(total);
    const speeds = new Float32Array(total);
    const swirl = new Float32Array(total);
    const colorObj = new THREE.Color();

    function assign(i: number) {
      const src = Math.floor(Math.random() * nodeCount);
      let dst = Math.floor(Math.random() * nodeCount);
      if (dst === src) dst = (src + 1) % nodeCount;
      fromIdx[i] = src;
      toIdx[i] = dst;
      tParam[i] = -Math.random() * 0.8;
      speeds[i] = 0.12 + Math.random() * 0.18;
      swirl[i] = 1.2 + Math.random() * 0.8;
      colorObj.set(ROLES[src].color);
      target[i * 3] = colorObj.r;
      target[i * 3 + 1] = colorObj.g;
      target[i * 3 + 2] = colorObj.b;
      sizes[i] = 2 + Math.random() * 2;
    }

    for (let i = 0; i < total; i++) assign(i);
    colors.set(target);

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("aColor", new THREE.BufferAttribute(colors, 3));
    geometry.setAttribute("aSize", new THREE.BufferAttribute(sizes, 1));

    const material = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      vertexShader: `
        attribute float aSize;
        attribute vec3 aColor;
        varying vec3 vColor;
        void main() {
          vColor = aColor;
          vec4 mv = modelViewMatrix * vec4(position, 1.0);
          gl_PointSize = aSize * (180.0 / -mv.z);
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: `
        varying vec3 vColor;
        void main() {
          vec2 uv = gl_PointCoord - 0.5;
          float d = length(uv);
          float alpha = smoothstep(0.5, 0.0, d);
          alpha *= alpha;
          gl_FragColor = vec4(vColor, alpha);
        }`,
    });

    scene.add(new THREE.Points(geometry, material));

    const glowGeo = new THREE.BufferGeometry();
    const glowPos = new Float32Array(nodeCount * 3);
    const glowCol = new Float32Array(nodeCount * 3);
    const glowTarget = new Float32Array(nodeCount * 3);
    const glowSize = new Float32Array(nodeCount);
    for (let i = 0; i < nodeCount; i++) glowSize[i] = 16 + (i % 3) * 4;
    glowGeo.setAttribute("position", new THREE.BufferAttribute(glowPos, 3));
    glowGeo.setAttribute("aColor", new THREE.BufferAttribute(glowCol, 3));
    glowGeo.setAttribute("aSize", new THREE.BufferAttribute(glowSize, 1));
    scene.add(new THREE.Points(glowGeo, material));

    const tmpColor = new THREE.Color();
    function updatePalette() {
      const healthy = stateRef.current.healthy;
      for (let i = 0; i < nodeCount; i++) {
        tmpColor.set(healthy ? ROLES[i].color : badColor(ROLES[i].color));
        glowTarget[i * 3] = tmpColor.r;
        glowTarget[i * 3 + 1] = tmpColor.g;
        glowTarget[i * 3 + 2] = tmpColor.b;
      }
      for (let i = 0; i < total; i++) {
        tmpColor.set(healthy ? ROLES[fromIdx[i]].color : badColor(ROLES[fromIdx[i]].color));
        target[i * 3] = tmpColor.r;
        target[i * 3 + 1] = tmpColor.g;
        target[i * 3 + 2] = tmpColor.b;
      }
    }
    updatePalette();

    const tmpA = new THREE.Vector3();
    const tmpB = new THREE.Vector3();
    const tmpC = new THREE.Vector3();
    const clock = new THREE.Clock();
    let raf = 0;
    let time = 0;

    function nodeWorld(idx: number, out: THREE.Vector3) {
      const role = ROLES[idx];
      const a = role.angle + time * 0.06;
      out.set(Math.cos(a) * role.radius, Math.sin(a * 1.7) * 0.35, Math.sin(a) * role.radius);
      return out;
    }

    function resize() {
      const w = host.clientWidth;
      const h = host.clientHeight;
      if (!w || !h) return;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    }
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(host);

    function frame() {
      raf = requestAnimationFrame(frame);
      const dt = Math.min(clock.getDelta(), 0.05);
      time += dt;
      const speedMul = (0.4 + stateRef.current.activity) * (reduced ? 0.15 : 1);
      scene.rotation.y += dt * 0.05;

      for (let i = 0; i < nodeCount; i++) nodeWorld(i, nodePos[i]);
      for (let i = 0; i < nodeCount; i++) {
        glowPos[i * 3] = nodePos[i].x;
        glowPos[i * 3 + 1] = nodePos[i].y;
        glowPos[i * 3 + 2] = nodePos[i].z;
      }
      glowGeo.attributes.position.needsUpdate = true;

      const k = Math.min(1, dt * 2.5);
      for (let i = 0; i < total * 3; i++) colors[i] += (target[i] - colors[i]) * k;
      geometry.attributes.aColor.needsUpdate = true;
      for (let i = 0; i < nodeCount * 3; i++) glowCol[i] += (glowTarget[i] - glowCol[i]) * k;
      glowGeo.attributes.aColor.needsUpdate = true;

      for (let i = 0; i < total; i++) {
        tParam[i] += dt * speeds[i] * speedMul;
        const t = tParam[i];
        if (t < 0) {
          const s = nodePos[fromIdx[i]];
          positions[i * 3] = s.x;
          positions[i * 3 + 1] = s.y;
          positions[i * 3 + 2] = s.z;
          continue;
        }
        if (t >= 1) {
          assign(i);
          tParam[i] = -0.05 * Math.random();
          continue;
        }
        const a = nodePos[fromIdx[i]];
        const b = nodePos[toIdx[i]];
        tmpC.copy(a).add(b).multiplyScalar(0.5 * swirl[i]);
        tmpC.y += Math.sin(time * 0.8 + i * 0.37) * 0.3;
        const u = 1 - t;
        tmpA.copy(a).multiplyScalar(u * u);
        tmpB.copy(tmpC).multiplyScalar(2 * u * t);
        tmpA.add(tmpB);
        tmpB.copy(b).multiplyScalar(t * t);
        tmpA.add(tmpB);
        positions[i * 3] = tmpA.x;
        positions[i * 3 + 1] = tmpA.y;
        positions[i * 3 + 2] = tmpA.z;
      }
      geometry.attributes.position.needsUpdate = true;

      renderer.render(scene, camera);
    }
    frame();

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      geometry.dispose();
      glowGeo.dispose();
      material.dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode === host) host.removeChild(renderer.domElement);
    };
  }, []);

  return <div ref={hostRef} className="swarm" aria-hidden="true" />;
}
