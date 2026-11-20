/**
 * GPU particle swarm for the hero, adapted from the Particles Casberry engine:
 * 20k instanced tetrahedra driven by GPGPU spring physics, UnrealBloom, and a
 * background composite pass. One frame loop, one draw call, bloom doing the
 * visibility work the old point-cloud version lacked.
 *
 * Adaptations for the dashboard: sized to its container (not the window) with
 * a ResizeObserver, speed changes accumulated smoothly instead of jumping the
 * clock, particle hues track portfolio health (green family vs red family),
 * reduced-motion halves the count, and dispose() releases everything.
 */

import * as THREE from "three";
import { GPUComputationRenderer } from "three/examples/jsm/misc/GPUComputationRenderer.js";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { ShaderPass } from "three/examples/jsm/postprocessing/ShaderPass.js";

const BgCompositeShader = {
  uniforms: {
    tDiffuse: { value: null },
    bgColor: { value: new THREE.Color("#020203") },
  },
  vertexShader: `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );
    }`,
  fragmentShader: `
    uniform sampler2D tDiffuse;
    uniform vec3 bgColor;
    varying vec2 vUv;
    void main() {
      vec4 texel = texture2D( tDiffuse, vUv );
      float alpha = clamp(max(texel.r, max(texel.g, texel.b)), 0.0, 1.0);
      gl_FragColor = vec4( texel.rgb + bgColor * (1.0 - alpha), 1.0 );
    }`,
};

/* eslint-disable @typescript-eslint/no-explicit-any */

export class ParticlesSwarm {
  private count: number;
  private container: HTMLElement;
  private speedMult = 1;
  private targetSpeed = 1;
  private hueBase = 0.4;
  private targetHue = 0.4;

  private physicsOverrides: any = {
    gravity: null,
    shockwaveCenter: null,
    shockwaveForce: null,
    shockwaveRadius: null,
    stiffness: null,
    damping: null,
    noiseStrength: null,
  };

  private scene: THREE.Scene;
  private camera: THREE.PerspectiveCamera;
  private renderer: THREE.WebGLRenderer;
  private composer: EffectComposer;
  private bloomPass: UnrealBloomPass;
  private bgCompositePass: ShaderPass;

  private dummy = new THREE.Object3D();
  private color = new THREE.Color();
  private target = new THREE.Vector3();
  private pColor = new THREE.Color();

  private geometry!: THREE.TetrahedronGeometry;
  private material!: THREE.MeshBasicMaterial;
  private mesh!: THREE.InstancedMesh;
  private targets: THREE.Vector3[] = [];

  private gpuCompute: any;
  private positionVariable: any;
  private velocityUniforms: any;
  private targetTex: THREE.DataTexture;
  private W = 0;

  private clock = new THREE.Clock();
  private time = 0;
  private running = true;
  private raf = 0;

  private raycaster = new THREE.Raycaster();
  private mouse = new THREE.Vector2();
  private dummyPlane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);
  private intersectPoint = new THREE.Vector3();
  private velocityVariableRef: any;

  private onPointerMove: (e: PointerEvent) => void;
  private resizeObserver?: ResizeObserver;

  constructor(container: HTMLElement, count?: number) {
    this.container = container;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.count =
      count ?? (window.innerWidth < 700 ? 9000 : 20000) / (reduced ? 2 : 1);
    this.targetSpeed = reduced ? 0.4 : 1;
    this.speedMult = this.targetSpeed;

    // SETUP — sized to the hero container, not the window.
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(60, 1, 0.1, 2000);
    this.camera.position.set(0, 0, 100);

    this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
    this.renderer.setClearColor(0x020203, 1);
    container.appendChild(this.renderer.domElement);

    // POST PROCESSING: bloom is what makes the swarm read at phone size.
    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloomPass = new UnrealBloomPass(
      new THREE.Vector2(container.clientWidth || window.innerWidth, container.clientHeight || window.innerHeight),
      1.5,
      0.4,
      0.85
    );
    this.bloomPass.strength = 0.7;
    this.bloomPass.radius = 0.35;
    this.bloomPass.threshold = 0.15;
    this.composer.addPass(this.bloomPass);

    this.bgCompositePass = new ShaderPass(BgCompositeShader);
    this.composer.addPass(this.bgCompositePass);
    this.composer.passes[this.composer.passes.length - 1].renderToScreen = true;

    this.fit();

    // OBJECTS
    this.geometry = new THREE.TetrahedronGeometry(0.25);
    const W = Math.ceil(Math.sqrt(this.count));
    this.W = W;
    const references = new Float32Array(this.count * 2);
    for (let i = 0; i < this.count; i++) {
      references[i * 2] = ((i % W) + 0.5) / W;
      references[i * 2 + 1] = (Math.floor(i / W) + 0.5) / W;
    }
    this.geometry.setAttribute("reference", new THREE.InstancedBufferAttribute(references, 2));

    const scales = new Float32Array(this.count);
    const opacities = new Float32Array(this.count);
    for (let i = 0; i < this.count; i++) {
      scales[i] = 1.0;
      opacities[i] = 1.0;
    }
    this.geometry.setAttribute("instanceScale", new THREE.InstancedBufferAttribute(scales, 1));
    this.geometry.setAttribute("instanceOpacity", new THREE.InstancedBufferAttribute(opacities, 1));

    this.material = new THREE.MeshBasicMaterial({ color: 0xffffff });

    // GPGPU hookup: positions come from the physics texture, not the matrix.
    this.material.onBeforeCompile = (shader: any) => {
      shader.uniforms.texturePosition = { value: null };
      shader.uniforms.gpgpuEnabled = { value: 1.0 };
      (this.material.userData as any).shader = shader;

      shader.vertexShader = `
        attribute vec2 reference;
        attribute float instanceScale;
        attribute float instanceOpacity;
        varying float vInstanceOpacity;
        uniform sampler2D texturePosition;
        uniform float gpgpuEnabled;
      ` + shader.vertexShader;

      shader.vertexShader = shader.vertexShader.replace(
        "#include <project_vertex>",
        `
        vec4 mvPosition = vec4( transformed, 1.0 );
        #ifdef USE_INSTANCING
          mvPosition.xyz *= instanceScale;
          vInstanceOpacity = instanceOpacity;
          mat4 m = instanceMatrix;
          if (gpgpuEnabled > 0.5) m[3] = vec4(0.0, 0.0, 0.0, 1.0);
          mvPosition = m * mvPosition;
          if (gpgpuEnabled > 0.5) {
            mvPosition.xyz += texture2D(texturePosition, reference).xyz;
          } else {
            mvPosition.xyz += vec3(instanceMatrix[3][0], instanceMatrix[3][1], instanceMatrix[3][2]);
          }
        #endif
        mvPosition = modelViewMatrix * mvPosition;
        gl_Position = projectionMatrix * mvPosition;
        `
      );

      shader.fragmentShader = `varying float vInstanceOpacity;\n` + shader.fragmentShader;
      shader.fragmentShader = shader.fragmentShader.replace(
        "#include <color_fragment>",
        `
        #include <color_fragment>
        diffuseColor.a *= vInstanceOpacity;
        `
      );
    };
    this.material.needsUpdate = true;

    this.mesh = new THREE.InstancedMesh(this.geometry, this.material, this.count);
    this.mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.scene.add(this.mesh);

    for (let i = 0; i < this.count; i++) {
      this.targets.push(new THREE.Vector3((Math.random() - 0.5) * 100, (Math.random() - 0.5) * 100, (Math.random() - 0.5) * 100));
      this.mesh.setColorAt(i, this.color.setHex(0x00ff88));
    }

    // GPGPU PHYSICS
    this.targetTex = new THREE.DataTexture(new Float32Array(W * W * 4), W, W, THREE.RGBAFormat, THREE.FloatType);
    this.updateGPGPUTargets();

    this.gpuCompute = new GPUComputationRenderer(W, W, this.renderer);
    const dtPosition = this.gpuCompute.createTexture();
    const dtVelocity = this.gpuCompute.createTexture();

    const posArray = (dtPosition.image as any).data as Float32Array;
    const velArray = (dtVelocity.image as any).data as Float32Array;
    for (let i = 0; i < W * W; i++) {
      const idx = i * 4;
      posArray[idx] = (Math.random() - 0.5) * 100;
      posArray[idx + 1] = (Math.random() - 0.5) * 100;
      posArray[idx + 2] = (Math.random() - 0.5) * 100;
      posArray[idx + 3] = 1.0;
      velArray[idx] = 0.0;
      velArray[idx + 1] = 0.0;
      velArray[idx + 2] = 0.0;
      velArray[idx + 3] = 0.0;
    }

    const posShader = `
      void main() {
        vec2 uv = gl_FragCoord.xy / resolution.xy;
        vec3 pos = texture2D(texturePosition, uv).xyz;
        vec3 vel = texture2D(textureVelocity, uv).xyz;
        pos += vel * 0.016;
        gl_FragColor = vec4(pos, 1.0);
      }`;

    const velShader = `
      uniform float uTime;
      uniform float uDeltaTime;
      uniform float uStiffness;
      uniform float uDamping;
      uniform float uNoiseStrength;
      uniform int uPhysicsMode;
      uniform vec3 uGravity;
      uniform vec3 uShockwaveCenter;
      uniform float uShockwaveForce;
      uniform float uShockwaveRadius;
      uniform sampler2D textureTarget;

      vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
      vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
      vec4 permute(vec4 x) { return mod289(((x*34.0)+1.0)*x); }
      vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }
      float snoise(vec3 v) {
        const vec2 C = vec2(1.0/6.0, 1.0/3.0);
        const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
        vec3 i  = floor(v + dot(v, vec3(C.y)));
        vec3 x0 = v - i + dot(i, vec3(C.x));
        vec3 g = step(x0.yzx, x0.xyz);
        vec3 l = 1.0 - g;
        vec3 i1 = min(g.xyz, l.zxy);
        vec3 i2 = max(g.xyz, l.zxy);
        vec3 x1 = x0 - i1 + vec3(C.x);
        vec3 x2 = x0 - i2 + vec3(C.y);
        vec3 x3 = x0 - D.yyy;
        i = mod289(i);
        vec4 p = permute(permute(permute(
                   i.z + vec4(0.0, i1.z, i2.z, 1.0))
                 + i.y + vec4(0.0, i1.y, i2.y, 1.0))
                 + i.x + vec4(0.0, i1.x, i2.x, 1.0));
        float n_ = 0.142857142857;
        vec3 ns = n_ * D.wyz - D.xzx;
        vec4 j = p - 49.0 * floor(p * ns.z);
        vec4 x_ = floor(j * ns.z);
        vec4 y_ = floor(j - 7.0 * x_);
        vec4 h = 1.0 - abs(x) - abs(y);
        vec4 b0 = vec4(x.xy, y.xy);
        vec4 b1 = vec4(x.zw, y.zw);
        vec4 s0 = floor(b0)*2.0 + 1.0;
        vec4 s1 = floor(b1)*2.0 + 1.0;
        vec4 sh = -step(h, vec4(0.0));
        vec4 a0 = b0.xzyw + s0.xzyw*sh.xxyy;
        vec4 a1 = b1.xzyw + s1.xzyw*sh.zzww;
        vec3 p0 = vec3(a0.xy, h.x);
        vec3 p1 = vec3(a0.zw, h.y);
        vec3 p2 = vec3(a1.xy, h.z);
        vec3 p3 = vec3(a1.zw, h.w);
        vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2,p2), dot(p3,p3)));
        p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
        vec4 m = max(0.6 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
        m = m * m;
        return 42.0 * dot(m*m, vec4(dot(p0,x0), dot(p1,x1), dot(p2,x2), dot(p3,x3)));
      }

      vec3 curlNoise(vec3 p) {
        const float e = 0.05;
        float dx = snoise(vec3(p.x + e, p.y, p.z)) - snoise(vec3(p.x - e, p.y, p.z));
        float dy = snoise(vec3(p.x, p.y + e, p.z)) - snoise(vec3(p.x, p.y - e, p.z));
        float dz = snoise(vec3(p.x, p.y, p.z + e)) - snoise(vec3(p.x, p.y, p.z - e));
        return vec3(dy - dz, dz - dx, dx - dy) / (2.0 * e);
      }

      void main() {
        vec2 uv = gl_FragCoord.xy / resolution.xy;
        vec3 pos = texture2D(texturePosition, uv).xyz;
        vec3 vel = texture2D(textureVelocity, uv).xyz;
        vec3 target = texture2D(textureTarget, uv).xyz;

        vec3 force = vec3(0.0);

        // 1. Target attraction: lerp mode chases the velocity the target implies.
        if (uPhysicsMode == 0) {
          vec3 desiredVel = (target - pos) * 10.0;
          force += (desiredVel - vel) * 20.0;
        } else {
          vec3 springForce = -uStiffness * (pos - target);
          force += springForce;
        }

        // 2. Curl noise / turbulence
        if (uNoiseStrength > 0.0) {
          float scale = 0.05;
          vec3 noiseOffset = curlNoise(pos * scale + vec3(0.0, 0.0, uTime * 0.2));
          force += noiseOffset * uNoiseStrength * 5.0;
        }

        // 3. Gravity override
        force += uGravity * 100.0;

        // 4. Shockwave (radial explosion)
        if (uShockwaveForce > 0.0) {
          float d = distance(pos, uShockwaveCenter);
          if (d < uShockwaveRadius && d > 0.1) {
            float shockStrength = (1.0 - (d / uShockwaveRadius)) * uShockwaveForce;
            vec3 shockDir = normalize(pos - uShockwaveCenter);
            force += shockDir * shockStrength * 500.0;
          }
        }

        vel += force * uDeltaTime;
        vel *= (1.0 - uDamping);

        float maxVel = 50.0;
        if (length(vel) > maxVel) vel = normalize(vel) * maxVel;

        gl_FragColor = vec4(vel, 1.0);
      }`;

    this.positionVariable = this.gpuCompute.addVariable("texturePosition", posShader, dtPosition);
    this.velocityVariableRef = this.gpuCompute.addVariable("textureVelocity", velShader, dtVelocity);

    this.gpuCompute.setVariableDependencies(this.positionVariable, [this.positionVariable, this.velocityVariableRef]);
    this.gpuCompute.setVariableDependencies(this.velocityVariableRef, [this.positionVariable, this.velocityVariableRef]);

    this.velocityUniforms = this.velocityVariableRef.material.uniforms;
    this.velocityUniforms.uTime = { value: 0.0 };
    this.velocityUniforms.uDeltaTime = { value: 0.016 };
    this.velocityUniforms.uPhysicsMode = { value: 0 }; // lerp mode
    this.velocityUniforms.uStiffness = { value: 2.0 };
    this.velocityUniforms.uDamping = { value: 0.08 };
    this.velocityUniforms.uNoiseStrength = { value: 0.0 };
    this.velocityUniforms.uGravity = { value: new THREE.Vector3() };
    this.velocityUniforms.uShockwaveCenter = { value: new THREE.Vector3() };
    this.velocityUniforms.uShockwaveForce = { value: 0.0 };
    this.velocityUniforms.uShockwaveRadius = { value: 0.0 };
    this.velocityUniforms.textureTarget = { value: this.targetTex };

    this.gpuCompute.init();

    // Pointer parallax source (physics mode is lerp; the mouse feeds shockwaves)
    this.onPointerMove = (e: PointerEvent) => {
      this.mouse.x = (e.clientX / window.innerWidth) * 2 - 1;
      this.mouse.y = -(e.clientY / window.innerHeight) * 2 + 1;
    };
    window.addEventListener("pointermove", this.onPointerMove);

    this.resizeObserver = new ResizeObserver(() => this.fit());
    this.resizeObserver.observe(container);

    this.animate = this.animate.bind(this);
    this.raf = requestAnimationFrame(this.animate);
  }

  /** Portfolio state: speed from trade activity, hue from health. */
  setState({ activity, healthy }: { activity: number; healthy: boolean }) {
    this.targetSpeed = 0.55 + Math.max(0.15, Math.min(1.6, activity)) * 1.1;
    this.targetHue = healthy ? 0.4 : 0.98;
  }

  setPhysics(options: any) {
    if (!options) return;
    if (options.gravity) {
      if (!this.physicsOverrides.gravity) this.physicsOverrides.gravity = new THREE.Vector3();
      this.physicsOverrides.gravity.copy(options.gravity);
    }
    if (options.shockwave) {
      if (options.shockwave.center) {
        if (!this.physicsOverrides.shockwaveCenter) this.physicsOverrides.shockwaveCenter = new THREE.Vector3();
        this.physicsOverrides.shockwaveCenter.copy(options.shockwave.center);
      }
      if (options.shockwave.force !== undefined) this.physicsOverrides.shockwaveForce = options.shockwave.force;
      if (options.shockwave.radius !== undefined) this.physicsOverrides.shockwaveRadius = options.shockwave.radius;
    }
    if (options.stiffness !== undefined) this.physicsOverrides.stiffness = options.stiffness;
    if (options.drag !== undefined) this.physicsOverrides.damping = options.drag;
    if (options.turbulence !== undefined) this.physicsOverrides.noiseStrength = options.turbulence;
  }

  private fit() {
    const w = this.container.clientWidth || window.innerWidth;
    const h = this.container.clientHeight || window.innerHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.composer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  private updateGPGPUTargets() {
    const data = (this.targetTex.image as any).data as Float32Array;
    const W = this.W;
    for (let i = 0; i < W * W; i++) {
      const idx = i * 4;
      if (i < this.count && i < this.targets.length) {
        const p = this.targets[i];
        data[idx] = p.x;
        data[idx + 1] = p.y;
        data[idx + 2] = p.z;
      } else {
        data[idx] = 999999.0;
        data[idx + 1] = 999999.0;
        data[idx + 2] = 999999.0;
      }
      data[idx + 3] = 1.0;
    }
    this.targetTex.needsUpdate = true;
  }

  private animate() {
    if (!this.running) return;
    this.raf = requestAnimationFrame(this.animate);
    const delta = Math.min(this.clock.getDelta(), 0.05);

    // Accumulate time so speed changes ease instead of jumping the clock.
    this.speedMult += (this.targetSpeed - this.speedMult) * Math.min(1, delta * 2.0);
    this.hueBase += (this.targetHue - this.hueBase) * Math.min(1, delta * 1.5);
    this.time += delta * this.speedMult;

    const count = this.count;
    const time = this.time;

    // Per-instance targets: a slow three-axis Lissajous drift, hue tinted by
    // portfolio health so the whole swarm warms toward red on a losing day.
    for (let i = 0; i < count; i++) {
      const t = time + i * 0.0001;
      this.target.set(Math.cos(t * 3) * 30, Math.sin(t * 2) * 30, Math.sin(t * 5) * 30);
      this.targets[i].copy(this.target);

      const hue = (this.hueBase + (i / count) * 0.14 - 0.07 + 1) % 1;
      const light = 0.5 + 0.2 * Math.sin(t * 2.0 + i);
      this.pColor.setHSL(hue, 0.85, light);
      this.mesh.setColorAt(i, this.pColor);

      this.dummy.position.set(0, 0, 0);
      this.dummy.rotation.set(0, 0, 0);
      this.dummy.updateMatrix();
      this.mesh.setMatrixAt(i, this.dummy.matrix);
    }
    this.mesh.instanceMatrix.needsUpdate = true;
    if (this.mesh.instanceColor) this.mesh.instanceColor.needsUpdate = true;

    this.updateGPGPUTargets();

    this.velocityUniforms.uTime.value = time;
    this.velocityUniforms.uDeltaTime.value = delta;
    this.velocityUniforms.uDamping.value =
      this.physicsOverrides.damping !== null ? this.physicsOverrides.damping : 0.08;
    this.velocityUniforms.uNoiseStrength.value =
      this.physicsOverrides.noiseStrength !== null ? this.physicsOverrides.noiseStrength : 0;
    if (this.physicsOverrides.gravity) {
      this.velocityUniforms.uGravity.value.copy(this.physicsOverrides.gravity);
    } else {
      this.velocityUniforms.uGravity.value.set(0, 0, 0);
    }
    if (this.physicsOverrides.shockwaveCenter && this.physicsOverrides.shockwaveForce !== null) {
      this.velocityUniforms.uShockwaveCenter.value.copy(this.physicsOverrides.shockwaveCenter);
      this.velocityUniforms.uShockwaveForce.value = this.physicsOverrides.shockwaveForce;
      this.velocityUniforms.uShockwaveRadius.value = this.physicsOverrides.shockwaveRadius;
    } else {
      this.velocityUniforms.uShockwaveForce.value = 0.0;
    }

    this.gpuCompute.compute();

    const posTexture = this.gpuCompute.getCurrentRenderTarget(this.positionVariable).texture;
    const shader = (this.material.userData as any).shader;
    if (shader) {
      shader.uniforms.texturePosition.value = posTexture;
      shader.uniforms.gpgpuEnabled.value = 1.0;
    }

    this.composer.render(delta);
  }

  setEffects(options: any) {
    if (!options) return;
    if (options.glowIntensity !== undefined) this.bloomPass.strength = options.glowIntensity;
    if (options.glowThreshold !== undefined) this.bloomPass.threshold = options.glowThreshold;
    if (options.bgColor !== undefined) {
      this.bgCompositePass.uniforms.bgColor.value.set(options.bgColor);
      document.body.style.background = options.bgColor;
    }
  }

  dispose() {
    this.running = false;
    cancelAnimationFrame(this.raf);
    window.removeEventListener("pointermove", this.onPointerMove);
    this.resizeObserver?.disconnect();
    try {
      this.geometry.dispose();
      this.material.dispose();
      this.scene.remove(this.mesh);
      this.mesh.dispose();
      this.gpuCompute.dispose?.();
      this.composer.dispose?.();
      this.renderer.dispose();
    } catch {
      /* best-effort teardown */
    }
    if (this.renderer.domElement.parentNode === this.container) {
      this.container.removeChild(this.renderer.domElement);
    }
  }
}
