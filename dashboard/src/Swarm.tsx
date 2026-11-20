import { useEffect, useRef } from "react";
import { ParticlesSwarm } from "./ParticlesSwarm";

/**
 * React shell for the GPU particle engine. One engine instance for the life
 * of the component; portfolio state flows in through setState(), never by
 * rebuilding the scene. If WebGL or GPGPU is unavailable on the device, the
 * hero degrades to the plain gradient — the page must never go blank.
 */
export default function Swarm({ activity, healthy }: { activity: number; healthy: boolean }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const engineRef = useRef<ParticlesSwarm | null>(null);
  const stateRef = useRef({ activity, healthy });

  useEffect(() => {
    stateRef.current = { activity, healthy };
    engineRef.current?.setState({ activity, healthy });
  }, [activity, healthy]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    try {
      engineRef.current = new ParticlesSwarm(host);
      engineRef.current.setState(stateRef.current);
    } catch (e) {
      console.warn("swarm engine unavailable:", e);
      engineRef.current = null;
    }
    return () => {
      engineRef.current?.dispose();
      engineRef.current = null;
    };
  }, []);

  return <div ref={hostRef} className="swarm" aria-hidden="true" />;
}
