"""Capture synthetic environment comparisons using the real local WebGPU viewer.

Use --source and --viewport to compare implementations without changing the
checkout. Reports stay local unless explicitly selected for publication.
Timing includes browser scheduling and GPU queue completion; it is not a
hardware GPU timestamp measurement.
"""

import argparse
import json
from pathlib import Path
import statistics
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app.runtime import server  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


SETUP = """async () => {
  const T = await import('three/webgpu');
  const s = await import('./js/scene/scene.js');
  await s.rendererReady;
  if (!s.isRendererAvailable()) throw new Error('A compatible WebGPU adapter is required.');
  const normal = new T.DataTexture(new Uint8Array([160, 120, 250, 255]), 1, 1);
  normal.needsUpdate = true;
  const materials = [
    new T.MeshStandardNodeMaterial({color:0x9eada2, roughness:0.8, normalMap:normal}),
    new T.MeshStandardNodeMaterial({color:0xcdd8df, roughness:0.18, metalness:1}),
    new T.MeshStandardNodeMaterial({color:0x98754a, roughness:0.12}),
    new T.MeshStandardNodeMaterial({color:0x5c6f96, roughness:0.35, transparent:true, opacity:0.55}),
    new T.MeshStandardNodeMaterial({color:0x9eada2, roughness:0.8}),
    new T.MeshToonNodeMaterial({color:0xd89061}),
    new T.MeshBasicNodeMaterial({color:0x638dbe}),
    new T.MeshStandardNodeMaterial({color:0x202020, emissive:0xff6633, emissiveIntensity:2}),
  ];
  const meshes = materials.map((material, i) => {
    const mesh = new T.Mesh(new T.SphereGeometry(0.48, 48, 32), material);
    mesh.position.set((i%4-1.5)*1.15, Math.floor(i/4)*1.2+0.5, 0);
    return mesh;
  });
  const pixels = new Uint8Array(16*16*4);
  for (let i=0;i<256;i++) pixels.set([180,120,70,i%4<2?255:0],i*4);
  const strands = new T.DataTexture(pixels,16,16);
  strands.colorSpace = T.SRGBColorSpace; strands.needsUpdate = true;
  const hair = new T.Mesh(new T.PlaneGeometry(0.85,0.7),
    new T.MeshStandardNodeMaterial({map:strands,alphaTest:0.5,side:T.DoubleSide,roughness:0.8}));
  hair.position.set(1.725,0.65,0.4); meshes.push(hair);
  for(const mesh of meshes) {
    mesh.layers.enable(1); mesh.castShadow = true; mesh.receiveShadow = true; s.scene.add(mesh);
  }
  window.environmentMeshes = meshes;
  s.fitTo(meshes); s.setBloomAvailable(true); s.toggleGrid();
  s.camera.position.set(0,1.45,6.8); s.controls.target.set(0,1,0);
  s.camera.lookAt(s.controls.target); s.controls.update();
  document.querySelector('#hint').remove();
  document.querySelector('#empty-actions').remove();
}"""

BRIDGE = """window.pywebview = {api: {
  get_viewer_preferences: async () => ({value:{}}),
  get_panel_opacity: async () => ({value:58}), get_language: async () => ({value:'en'}),
  get_mod_folders: async () => ({folders:[]}), get_asset_folders: async () => ({folders:[]}),
  consume_startup_request: async () => null,
}};"""


def evaluate(page, body, arg=None):
    return page.evaluate(
        """async arg => {
      const s = await import('./js/scene/scene.js');
      const {requestRender} = await import('./js/scene/render-scheduler.js');
      const frame = async () => {
        requestRender(); await new Promise(requestAnimationFrame);
        await s.renderer.backend.device.queue.onSubmittedWorkDone();
        if (!s.isRendererAvailable()) throw new Error('WebGPU rendering failed.');
      };
    """
        + body
        + "}",
        arg,
    )


def capture(browser, url, output, source, viewport):
    with browser.new_context(
        bypass_csp=True, viewport=dict(zip(("width", "height"), viewport))
    ) as context:
        context.add_init_script(BRIDGE)
        context.route(
            "**/js/scene/environment.js",
            lambda route: route.fulfill(content_type="text/javascript", body=source),
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        started = time.perf_counter()
        page.goto(url)
        page.wait_for_function("window.modViewer !== undefined")
        evaluate(
            page,
            "await s.rendererReady; while(s.getEnvironmentDebugState().preparationInFlight) await new Promise(r=>setTimeout(r,0)); await frame();",
        )
        report = {"startupMs": (time.perf_counter() - started) * 1000, "presets": {}}
        page.evaluate(SETUP)
        evaluate(page, "await frame();")
        report["modelSamples"] = evaluate(
            page,
            """
          return environmentMeshes.slice(0,8).map(mesh => {
            const center = mesh.getWorldPosition(mesh.position.clone()).project(s.camera);
            const edge = mesh.getWorldPosition(mesh.position.clone()); edge.x += 0.3; edge.project(s.camera);
            const rect = s.renderer.domElement.getBoundingClientRect();
            return {x:rect.left+(center.x+1)*rect.width/2,y:rect.top+(1-center.y)*rect.height/2,
              radius:Math.abs(edge.x-center.x)*rect.width/2};
          });
        """,
        )
        for preset in ["outdoor", "sunset"]:
            data = evaluate(
                page,
                """
              const start=performance.now();
              if(!window.modViewer.setEnvironmentPreset(arg)) throw new Error(`Unsupported preset: ${arg}`);
              while(s.getEnvironmentDebugState().preparationInFlight) await new Promise(r=>setTimeout(r,0));
              await frame();
              const sky=s.scene.background, env=s.scene.environment;
              return {activationMs:performance.now()-start, skyTexelBytes:sky?.isCubeTexture?6*sky.image[0].width**2*8:0,
                pmremTexelBytes:env?env.image.width*env.image.height*8:0};
            """,
                preset,
            )
            for mode in ["direct", "ao", "bloom", "ao-bloom"]:
                evaluate(
                    page,
                    """
                  s.setAmbientOcclusionStrength(arg.includes('ao')?0.5:0);
                  s.setBloomEnabled(arg.includes('bloom')); await frame();
                """,
                    mode,
                )
                page.screenshot(path=str(output / f"{preset}-{mode}.png"))
            evaluate(
                page,
                "s.setAmbientOcclusionStrength(0); s.setBloomEnabled(false); await frame();",
            )
            before = evaluate(
                page,
                "return [s.getRenderCount(),s.getCharacterShadowDebugState().shadowUpdateCount];",
            )
            page.wait_for_timeout(250)
            after = evaluate(
                page,
                "return [s.getRenderCount(),s.getCharacterShadowDebugState().shadowUpdateCount];",
            )
            data["idleUpdates"] = [a - b for a, b in zip(after, before)]
            data["cameraMotionMs"] = evaluate(
                page,
                """
              const position=s.camera.position.clone(), target=s.controls.target.clone();
              const offset=position.clone().sub(target), radius=Math.hypot(offset.x,offset.z);
              const phase=Math.atan2(offset.x,offset.z), timings=[];
              for(let i=0;i<35;i++) {
                const angle=phase+Math.sin(i/34*Math.PI*2)*0.2;
                s.camera.position.set(target.x+Math.sin(angle)*radius,position.y,target.z+Math.cos(angle)*radius);
                s.camera.lookAt(target);
                const start=performance.now(); await frame();
                if(i>=5) timings.push(performance.now()-start);
              }
              s.camera.position.copy(position); s.camera.lookAt(target); await frame();
              return timings;
            """,
            )
            report["presets"][preset] = data
        report["cachedMemoryBefore"] = evaluate(
            page, "return {...s.renderer.info.memory};"
        )
        report["cachedSwitchMs"] = evaluate(
            page,
            """
          const timings={outdoor:[],sunset:[]};
          for(let i=0;i<5;i++) for(const preset of Object.keys(timings)) {
            const start=performance.now(); s.setEnvironmentPreset(preset); await frame();
            timings[preset].push(performance.now()-start);
          }
          return timings;
        """,
        )
        report["cachedMemoryAfter"] = evaluate(
            page, "return {...s.renderer.info.memory};"
        )
        if errors:
            raise RuntimeError("Browser errors: " + "; ".join(errors))
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".quality-reports/environments"
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--source", type=Path, help="Serve a saved environment module for comparison"
    )
    parser.add_argument(
        "--viewport",
        type=int,
        nargs=2,
        default=[1280, 900],
        metavar=("WIDTH", "HEIGHT"),
    )
    args = parser.parse_args()
    if args.repeats < 1 or min(args.viewport) < 1:
        parser.error("Repeat count and viewport dimensions must be positive")
    source_path = args.source or ROOT / "src/web/js/scene/environment.js"
    source = source_path.read_text(encoding="utf-8")
    args.output.mkdir(parents=True, exist_ok=True)
    reports = []
    url = server.start()
    with sync_playwright() as runtime:
        for index in range(args.repeats):
            output = args.output / f"run-{index + 1}"
            output.mkdir(exist_ok=True)
            with runtime.chromium.launch(
                channel="msedge",
                headless=True,
                args=[
                    "--no-proxy-server",
                    "--enable-unsafe-webgpu",
                    "--disable-gpu-sandbox",
                    "--ignore-gpu-blocklist",
                    "--use-webgpu-adapter=d3d11",
                ],
            ) as browser:
                report = capture(browser, url, output, source, args.viewport)
            (output / "metrics.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
            reports.append(report)
            print(f"Captured run {index + 1}", flush=True)
    summary = {
        "viewport": args.viewport,
        "runs": reports,
        "medians": {
            preset: {
                "activationMs": statistics.median(
                    r["presets"][preset]["activationMs"] for r in reports
                ),
                "cachedSwitchMs": statistics.median(
                    v for r in reports for v in r["cachedSwitchMs"][preset]
                ),
                "skyTexelBytes": reports[0]["presets"][preset]["skyTexelBytes"],
                "pmremTexelBytes": reports[0]["presets"][preset]["pmremTexelBytes"],
                "cameraMotionMs": statistics.median(
                    v for r in reports for v in r["presets"][preset]["cameraMotionMs"]
                ),
            }
            for preset in ["outdoor", "sunset"]
        },
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary["medians"], indent=2))


if __name__ == "__main__":
    main()
