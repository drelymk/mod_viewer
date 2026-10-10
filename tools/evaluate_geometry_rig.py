"""Capture a geometry-rig comparison in the existing local viewer.

Requires development dependencies and a compatible browser/WebGPU runtime.
Reference guides are normalized semantic coordinates keyed by control name.
"""

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    from app.mods.loader import load_mod
    from core.geometry.transport import GeometryBlob
    from tests.web.support import (
        edge_browser,
        frontend_url,
        open_model,
        viewer,
        wait_loaded,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mod", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hide", action="append", default=[])
    parser.add_argument("--guides", type=Path)
    parser.add_argument("--check-duplicates", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    blob = GeometryBlob()
    payload = load_mod(str(args.mod), geometry=blob)
    if payload.get("error"):
        raise RuntimeError(payload["error"])
    payload["_fixture_blob"] = bytes(blob.data)
    url = frontend_url.__wrapped__()
    browser_fixture = edge_browser.__wrapped__()
    browser = next(browser_fixture)
    viewer_fixture = viewer.__wrapped__(browser, url)
    create = next(viewer_fixture)
    try:
        page = create(
            {"evaluation": payload},
            preferences={"textureMode": "none", "outlines": False},
        )
        open_model(page, "evaluation")
        wait_loaded(page, len(payload["meshes"]))
        page.locator("#weight-rig-tab").click()
        page.locator(".rig-preview-geometry").evaluate(
            "button => button.closest('details').open = true"
        )
        page.evaluate(
            """patterns => {
              for (const mesh of window.modViewer.activeMeshes) {
                if (patterns.some(pattern => mesh.userData.semanticKey.includes(pattern))) mesh.visible = false;
              }
            }""",
            args.hide,
        )
        page.locator(".rig-preview-geometry").evaluate("button => button.click()")
        page.evaluate("""async () => {
          window.__evaluationRigApi = (await import('./js/weight-rig/weight-rig-core.js')).weightRigApi;
        }""")
        page.wait_for_function(
            """() => {
              const preview = window.__evaluationRigApi.getModelRigState().geometryRigPreview;
              return preview && !preview.busy;
            }""",
            timeout=60000,
        )
        result = page.evaluate(
            """async () => {
              const {weightRigApi} = await import('./js/weight-rig/weight-rig-core.js');
              const {getModelTransformState} = await import('./js/scene/scene.js');
              const preview = weightRigApi.getModelRigState().geometryRigPreview;
              return {preview, orientation:getModelTransformState(),
                weightCalls:window.__bridge.calls.filter(call=>call.name==='weights').length,
                meshes:window.modViewer.activeMeshes.map(mesh=>({key:mesh.userData.semanticKey,visible:mesh.visible,
                  triangles:mesh.geometry.index.count/3}))};
            }"""
        )
        if not result["preview"].get("rig"):
            raise RuntimeError(result["preview"].get("errorKey", "No fitted rig"))
        result["projection"] = page.evaluate("""async () => {
          const THREE=await import('three');
          const {camera,renderer}=await import('./js/scene/scene.js');
          const mesh=window.modViewer.activeMeshes.find(mesh=>mesh.visible);
          mesh.updateWorldMatrix(true,false);
          const rect=renderer.domElement.getBoundingClientRect();
          const project=position=>{
            const p=new THREE.Vector3(...position).applyMatrix4(mesh.matrixWorld).project(camera);
            return [(p.x+1)*rect.width/2,(1-p.y)*rect.height/2];
          };
          const rig=window.__evaluationRigApi.getModelRigState().geometryRigPreview.rig;
          const {semanticToHumanoidControlPosition}=await import('./js/weight-rig/humanoid-control-rig.js');
          const anchor=y=>project(semanticToHumanoidControlPosition({sideN:0,height01:y,depthN:0},rig));
          return {heightPixels:Math.abs(anchor(1)[1]-anchor(0)[1]),top:anchor(1),bottom:anchor(0),
            controls:Object.fromEntries(Object.entries(rig.controls).map(([key,c])=>[key,project(c.position)])),
            proportional:Object.fromEntries(Object.entries(rig.proportionalRig.controls).map(([key,c])=>[key,project(c.position)]))};
        }""")
        if args.check_duplicates:
            result["duplicateCheck"] = page.evaluate("""async () => {
              const THREE=await import('three');
              const meshes=window.modViewer.activeMeshes;
              const duplicates=meshes.filter(mesh=>mesh.visible).map(mesh=>{
                const duplicate=new THREE.Mesh(mesh.geometry.clone(),mesh.material);
                duplicate.position.copy(mesh.position);duplicate.quaternion.copy(mesh.quaternion);duplicate.scale.copy(mesh.scale);
                duplicate.userData.humanoidRestPositions=new Float32Array(mesh.userData.humanoidRestPositions);
                return duplicate;
              });
              const {fitHumanoidGeometryRig}=await import('./js/weight-rig/humanoid-geometry-fit.js');
              const {getModelTransformState}=await import('./js/scene/scene.js');
              const fit=await fitHumanoidGeometryRig({meshes:[...meshes,...duplicates],orientationState:getModelTransformState()});
              const original=window.__evaluationRigApi.getModelRigState().geometryRigPreview.rig;
              const deviation=Math.max(...Object.keys(original.controls).map(key=>Math.hypot(...
                original.controls[key].position.map((v,i)=>v-fit.controls[key].position[i]))/original.frame.height));
              duplicates.forEach(mesh=>mesh.geometry.dispose());
              return {maxDeviationHeight:deviation,diagnostics:fit.diagnostics};
            }""")
        guides = (
            json.loads(args.guides.read_text(encoding="utf-8")) if args.guides else {}
        )
        if guides:
            from math import hypot

            projection = result["projection"]
            height = projection["heightPixels"]
            center, bottom = projection["bottom"]
            result["comparison"] = {}
            for key, guide in guides.items():

                def error(point):
                    return hypot(
                        (point[0] - center) / height - guide["sideN"],
                        (bottom - point[1]) / height - guide["height01"],
                    )

                result["comparison"][key] = {
                    "proportional": error(projection["proportional"][key]),
                    "geometry": error(projection["controls"][key]),
                    "visible": guide["visible"],
                    "confidence": result["preview"]["rig"]["controls"][key][
                        "confidence"
                    ],
                }
        (args.output / "comparison.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
        for mode in ["geometry", "proportional"]:
            page.locator(".rig-preview-comparison").select_option(mode)
            page.evaluate(
                """async () => {
                  const {renderer,camera,scene}=await import('./js/scene/scene.js');
                  await renderer.renderAsync(scene,camera);
                }"""
            )
            page.locator("#canvas-container").screenshot(
                path=str(args.output / f"{mode}.png")
            )
        print(
            json.dumps(
                {
                    "controls": len(result["preview"]["rig"]["controls"]),
                    "weightCalls": result["weightCalls"],
                    "fitRuntimeMs": result["preview"]["diagnostics"]["fitRuntimeMs"],
                    "sampledPointCount": result["preview"]["diagnostics"][
                        "sampledPointCount"
                    ],
                    "duplicateDeviation": result.get("duplicateCheck", {}).get(
                        "maxDeviationHeight"
                    ),
                }
            )
        )
    finally:
        for fixture in [viewer_fixture, browser_fixture]:
            try:
                next(fixture)
            except StopIteration:
                pass


if __name__ == "__main__":
    main()
