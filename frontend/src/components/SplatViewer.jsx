import { useEffect, useRef } from 'react';
import * as SPLAT from 'gsplat';

/**
 * Interactive gaussian-splat viewer (WebGL via gsplat). Whenever `modelUrl`
 * changes (each refinement checkpoint, then the final model) the scene is
 * swapped in place, so the model visibly forms while the pipeline runs.
 */
export default function SplatViewer({ modelUrl }) {
  const hostRef = useRef(null);
  const stateRef = useRef(null);

  useEffect(() => {
    const host = hostRef.current;
    const canvas = document.createElement('canvas');
    host.appendChild(canvas);

    const renderer = new SPLAT.WebGLRenderer(canvas);
    const scene = new SPLAT.Scene();
    const camera = new SPLAT.Camera();
    const controls = new SPLAT.OrbitControls(camera, canvas, 0, 0, 4);

    const state = {
      renderer,
      scene,
      camera,
      controls,
      raf: 0,
      // Serialize scene loads: checkpoints can arrive faster than they parse,
      // and only the newest queued URL matters — older ones are skippable.
      busy: false,
      nextUrl: null,
      disposed: false,
    };
    stateRef.current = state;

    const resize = () => renderer.setSize(host.clientWidth, host.clientHeight);
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(host);

    const frame = () => {
      controls.update();
      renderer.render(state.scene, camera);
      state.raf = requestAnimationFrame(frame);
    };
    state.raf = requestAnimationFrame(frame);

    return () => {
      state.disposed = true;
      cancelAnimationFrame(state.raf);
      observer.disconnect();
      renderer.dispose();
      canvas.remove();
      stateRef.current = null;
    };
  }, []);

  useEffect(() => {
    const state = stateRef.current;
    if (!state || !modelUrl) return;
    state.nextUrl = modelUrl;
    pumpLoads();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelUrl]);

  async function pumpLoads() {
    const state = stateRef.current;
    if (!state || state.busy || !state.nextUrl) return;

    const url = state.nextUrl;
    state.nextUrl = null;
    state.busy = true;
    try {
      state.scene.reset();
      await SPLAT.PLYLoader.LoadAsync(url, state.scene);
    } catch (err) {
      console.error('Failed to load splat scene', url, err);
    } finally {
      state.busy = false;
    }
    if (!state.disposed && state.nextUrl) pumpLoads();
  }

  return <div className="splat-viewer" ref={hostRef} />;
}
