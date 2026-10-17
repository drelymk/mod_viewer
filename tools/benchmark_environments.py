"""Capture synthetic environment comparisons using the real local WebGPU viewer.

Use --environment-ref with a Git revision to compare the environment module
without changing the checkout. Reports stay local unless explicitly selected
for publication. Timing includes browser scheduling and GPU queue completion;
it is not a hardware GPU timestamp measurement.
"""

import argparse
import json
from pathlib import Path
import statistics
import subprocess
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
  window.environmentScene = s;
  const normal = new T.DataTexture(new Uint8Array([160, 120, 250, 255]), 1, 1);
  normal.needsUpdate = true;
  const materials = [
    new T.MeshStandardNodeMaterial({color:0x9eada2, roughness:0.8, normalMap:normal}),
    new T.MeshStandardNodeMaterial({color:0xcdd8df, roughness:0.18, metalness:1}),
    new T.MeshStandardNodeMaterial({color:0x5c6f96, roughness:0.35, transparent:true, opacity:0.55}),
    new T.MeshToonNodeMaterial({color:0xd89061}),
    new T.MeshBasicNodeMaterial({color:0x638dbe}),
    new T.MeshStandardNodeMaterial({color:0x202020, emissive:0xff6633, emissiveIntensity:2}),
  ];
  const meshes = materials.map((material, i) => {
    const mesh = new T.Mesh(new T.SphereGeometry(0.48, 48, 32), material);
    mesh.position.set((i%3-1)*1.25, Math.floor(i/3)*1.2+0.5, 0);
    return mesh;
  });
  const pixels = new Uint8Array(16*16*4);
  for (let i=0;i<256;i++) pixels.set([180,120,70,i%4<2?255:0],i*4);
  const strands = new T.DataTexture(pixels,16,16);
  strands.colorSpace = T.SRGBColorSpace; strands.needsUpdate = true;
  const hair = new T.Mesh(new T.PlaneGeometry(0.85,0.7),
    new T.MeshStandardNodeMaterial({map:strands,alphaTest:0.5,side:T.DoubleSide,roughness:0.8}));
  hair.position.set(1.25,0.65,0.4); meshes.push(hair);
  for(const mesh of meshes) {
    mesh.layers.enable(1); mesh.castShadow = true; mesh.receiveShadow = true; s.scene.add(mesh);
  }
  s.fitTo(meshes); s.setBloomAvailable(true); s.toggleGrid();
  s.camera.position.set(0,1.45,6.8); s.controls.target.set(0,1,0);
  s.camera.lookAt(s.controls.target); s.controls.update();
  document.querySelector('#hint').remove();
  document.querySelector('#empty-actions').remove();
}"""

FRAME = """async () => {
  const s = environmentScene;
  const {requestRender} = await import('./js/scene/render-scheduler.js');
  requestRender(); await new Promise(requestAnimationFrame);
  await s.renderer.backend.device.queue.onSubmittedWorkDone();
}"""

BRIDGE = """window.pywebview = {api: {
  get_viewer_preferences: async () => ({value:{}}),
  get_panel_opacity: async () => ({value:58}), get_language: async () => ({value:'en'}),
  get_mod_folders: async () => ({folders:[]}), get_asset_folders: async () => ({folders:[]}),
  consume_startup_request: async () => null,
}};"""


def capture(browser, url, output, environment_source):
    with browser.new_context(
        bypass_csp=True, viewport={"width": 1280, "height": 900}
    ) as context:
        context.add_init_script(BRIDGE)
        if environment_source is not None:
            context.route(
                "**/js/scene/environment.js",
                lambda route: route.fulfill(
                    content_type="text/javascript", body=environment_source
                ),
            )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        started = time.perf_counter()
        page.goto(url)
        page.wait_for_function("window.modViewer !== undefined")
        page.evaluate("""async () => {
          const s = await import('./js/scene/scene.js'); await s.rendererReady;
          window.environmentScene = s;
        }""")
        page.wait_for_function(
            "!environmentScene.getEnvironmentDebugState().preparationInFlight"
        )
        page.evaluate(FRAME)
        result = {
            "startupMs": (time.perf_counter() - started) * 1000,
            "startupEnvironment": page.evaluate(
                "environmentScene.getEnvironmentDebugState()"
            ),
            "presets": {},
        }
        page.evaluate(SETUP)
        page.evaluate(FRAME)
        presets = page.evaluate("""async () => Object.keys(
          (await import('./js/scene/environment.js')).ENVIRONMENT_PRESETS)""")
        for preset in presets:
            started = time.perf_counter()
            page.evaluate("id => window.modViewer.setEnvironmentPreset(id)", preset)
            page.wait_for_function(
                "!environmentScene.getEnvironmentDebugState().preparationInFlight"
            )
            page.evaluate(FRAME)
            data = {"firstActivationMs": (time.perf_counter() - started) * 1000}
            for mode in ["direct", "ao", "bloom", "ao-bloom"]:
                page.evaluate(
                    """mode => {
                  const s = environmentScene;
                  s.setAmbientOcclusionStrength(mode.includes('ao')?0.5:0);
                  s.setBloomEnabled(mode.includes('bloom'));
                }""",
                    mode,
                )
                page.evaluate(FRAME)
                page.screenshot(path=str(output / f"{preset}-{mode}.png"))
            page.evaluate(
                "environmentScene.setAmbientOcclusionStrength(0); environmentScene.setBloomEnabled(false)"
            )
            data["cameraFrameMs"] = page.evaluate("""async () => {
              const s = environmentScene, times=[];
              const {requestRender}=await import('./js/scene/render-scheduler.js');
              for(let i=0;i<30;i++) {
                const start=performance.now();
                s.camera.position.set(Math.sin(i*0.015)*6.8,1.45,Math.cos(i*0.015)*6.8);
                s.camera.lookAt(s.controls.target); requestRender();
                await new Promise(requestAnimationFrame);
                await s.renderer.backend.device.queue.onSubmittedWorkDone();
                times.push(performance.now()-start);
              }
              s.camera.position.set(0,1.45,6.8); s.camera.lookAt(s.controls.target);
              s.controls.update(); requestRender();
              return times;
            }""")
            page.evaluate(FRAME)
            before = page.evaluate("""() => ({frames:environmentScene.getRenderCount(),
              shadows:environmentScene.getCharacterShadowDebugState().shadowUpdateCount})""")
            page.wait_for_timeout(250)
            after = page.evaluate("""() => ({frames:environmentScene.getRenderCount(),
              shadows:environmentScene.getCharacterShadowDebugState().shadowUpdateCount})""")
            data["idleFrames"] = after["frames"] - before["frames"]
            data["idleShadowUpdates"] = after["shadows"] - before["shadows"]
            data.update(
                page.evaluate("""() => ({memory:{...environmentScene.renderer.info.memory},
              environment:environmentScene.getEnvironmentDebugState()})""")
            )
            result["presets"][preset] = data
        result["cachedMemoryBefore"] = page.evaluate(
            "({...environmentScene.renderer.info.memory})"
        )
        result["cachedSwitchFrameMs"] = page.evaluate(
            """async presets => {
          const s = environmentScene, times=[];
          const {requestRender}=await import('./js/scene/render-scheduler.js');
          for(let i=0;i<3;i++) for(const preset of presets) {
            const start=performance.now(); s.setEnvironmentPreset(preset); requestRender();
            await new Promise(requestAnimationFrame);
            await s.renderer.backend.device.queue.onSubmittedWorkDone(); times.push(performance.now()-start);
          }
          return times;
        }""",
            presets,
        )
        result["cachedMemoryAfter"] = page.evaluate(
            "({...environmentScene.renderer.info.memory})"
        )
        if errors:
            raise RuntimeError("Browser errors: " + "; ".join(errors))
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".quality-reports/environments"
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--environment-ref", help="Git revision supplying environment.js"
    )
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    source = None
    if args.environment_ref:
        source = subprocess.run(
            ["git", "show", f"{args.environment_ref}:src/web/js/scene/environment.js"],
            cwd=ROOT,
            capture_output=True,
            encoding="utf-8",
            check=True,
        ).stdout
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
                report = capture(browser, url, output, source)
            (output / "metrics.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
            reports.append(report)
            print(f"Run {index + 1}: captured {len(report['presets'])} presets")
    summary = {
        "startupMedianMs": statistics.median(report["startupMs"] for report in reports),
        "presets": {
            preset: {
                "activationMedianMs": statistics.median(
                    report["presets"][preset]["firstActivationMs"] for report in reports
                ),
                "cameraFrameMedianMs": statistics.median(
                    value
                    for report in reports
                    for value in report["presets"][preset]["cameraFrameMs"]
                ),
                "idleFrames": [
                    report["presets"][preset]["idleFrames"] for report in reports
                ],
                "idleShadowUpdates": [
                    report["presets"][preset]["idleShadowUpdates"] for report in reports
                ],
            }
            for preset in reports[0]["presets"]
        },
        "cachedSwitchFrameMedianMs": statistics.median(
            value for report in reports for value in report["cachedSwitchFrameMs"]
        ),
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
