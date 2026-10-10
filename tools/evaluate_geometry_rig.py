"""Capture front/side orthographic comparisons in the existing local viewer.

Requires development dependencies and a compatible browser/WebGPU runtime.
Guides are normalized by character height. Only visible guides produce
accuracy errors; uncertain joints retain plausibility measurements.
"""

import argparse
import json
from math import dist, hypot
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


CAPTURE_VIEW = """async ({view, mode, baseline}) => {
  const THREE=await import('three');
  const {camera,renderer,scene,controls}=await import('./js/scene/scene.js');
  const {weightRigApi}=await import('./js/weight-rig/weight-rig-core.js');
  const {semanticToHumanoidControlPosition,rebuildHumanoidControlPaths}=
    await import('./js/weight-rig/humanoid-control-rig.js');
  weightRigApi.setGeometryRigPreviewMode(mode==='proportional'?'proportional':'geometry');
  const rig=weightRigApi.getModelRigState().geometryRigPreview.rig;
  const saved=structuredClone(rig.controls);
  if (baseline) {
    for (const [key,control] of Object.entries(rig.controls)) control.position=[...baseline[key]];
    rebuildHumanoidControlPaths(rig);weightRigApi.setGeometryRigPreviewMode('geometry');
  }
  const mesh=window.modViewer.activeMeshes.find(mesh=>mesh.visible);
  mesh.updateWorldMatrix(true,false);
  const matrix=mesh.matrixWorld;
  const rect=renderer.domElement.getBoundingClientRect();
  const point=semantic=>new THREE.Vector3(...semanticToHumanoidControlPosition(semantic,rig)).applyMatrix4(matrix);
  const bottom=point({sideN:0,height01:0,depthN:0});
  const top=point({sideN:0,height01:1,depthN:0});
  const height=top.distanceTo(bottom);
  const center=top.clone().add(bottom).multiplyScalar(0.5);
  const up=new THREE.Vector3(...rig.frame.up).transformDirection(matrix);
  const direction=new THREE.Vector3(...(view==='front'?rig.frame.forward:rig.frame.right.map(v=>-v)))
    .transformDirection(matrix);
  controls.enabled=false;
  // Retain the camera identity used by the existing overlay controller, while
  // applying an orthographic camera and its projection implementation.
  const half=height*0.64, aspect=rect.width/rect.height;
  Object.assign(camera,{isPerspectiveCamera:false,isOrthographicCamera:true,
    left:-half*aspect,right:half*aspect,top:half,bottom:-half,
    near:height*0.01,far:height*20,zoom:1,view:null});
  camera.updateProjectionMatrix=THREE.OrthographicCamera.prototype.updateProjectionMatrix;
  camera.position.copy(center).addScaledVector(direction,height*4);
  camera.up.copy(up);camera.lookAt(center);camera.updateProjectionMatrix();camera.updateMatrixWorld(true);
  controls.target.copy(center);controls.setCamera(camera);
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  await renderer.renderAsync(scene,camera);
  const project=position=>{
    const p=new THREE.Vector3(...position).applyMatrix4(matrix).project(camera);
    return [(p.x+1)*rect.width/2,(1-p.y)*rect.height/2];
  };
  const projectedBottom=project(semanticToHumanoidControlPosition({sideN:0,height01:0,depthN:0},rig));
  const projectedTop=project(semanticToHumanoidControlPosition({sideN:0,height01:1,depthN:0},rig));
  const pixels=Math.abs(projectedBottom[1]-projectedTop[1]);
  const positions=Object.fromEntries(Object.entries(rig.controls).map(([key,c])=>{
    const p=project(c.position);
    return [key,{horizontalN:(p[0]-projectedBottom[0])/pixels,
      height01:(projectedBottom[1]-p[1])/pixels,pixels:p}];
  }));
  window.__evaluationRestore=()=>{rig.controls=saved;rebuildHumanoidControlPaths(rig);};
  return {positions,heightPixels:pixels,bottom:projectedBottom,top:projectedTop,
    viewport:{width:rect.width,height:rect.height},projection:'orthographic',
    horizontalAxis:view==='front'?'right':'forward'};
}"""


def add_comparison(result, guides, baseline=None):
    """Summarize all controls without turning uncertain guides into truth."""
    changes = {"improved": [], "regressed": [], "stable": [], "uncertain": []}
    for view, capture in result["views"].items():
        comparison = {}
        for key, position in capture["geometry"]["positions"].items():
            guide = guides.get(key, {}).get(view)
            kind = (guide or {}).get("kind", "indeterminate")
            row = {
                "referenceKind": kind,
                "geometryErrorPercent": None,
                "proportionalErrorPercent": None,
                "iteration1ErrorPercent": None,
                "changePercent": None,
                "assessment": "uncertain",
            }
            if guide and guide.get("surfaceEnvelope"):
                envelope = guide["surfaceEnvelope"]
                row["outsideSurfaceEnvelope"] = any(
                    position[axis] < limits[0] or position[axis] > limits[1]
                    for axis, limits in envelope.items()
                )
            if kind == "visible":

                def error(point):
                    return (
                        hypot(
                            point["horizontalN"] - guide["horizontalN"],
                            point["height01"] - guide["height01"],
                        )
                        * 100
                    )

                row["geometryErrorPercent"] = error(position)
                row["proportionalErrorPercent"] = error(
                    capture["proportional"]["positions"][key]
                )
                if baseline:
                    original = error(
                        baseline["views"][view]["geometry"]["positions"][key]
                    )
                    row["iteration1ErrorPercent"] = original
                    row["changePercent"] = row["geometryErrorPercent"] - original
                    uncertainty = guide.get("uncertaintyHeight", 0.01) * 100
                    row["assessment"] = (
                        "improved"
                        if row["changePercent"] < -uncertainty
                        else "regressed"
                        if row["changePercent"] > uncertainty
                        else "stable"
                    )
            comparison[key] = row
            changes[row["assessment"]].append(f"{view}:{key}")
        capture["comparison"] = comparison
    result["summary"] = changes
    controls = result["controls"]["geometry"]
    scale = result["frame"]["height"]
    plausibility = {}
    for side in ("left", "right"):
        for limb, keys in (
            ("arm", ("Shoulder", "Elbow", "Hand")),
            ("leg", ("Hip", "Knee", "Foot")),
        ):
            a, b, c = [controls[side + key] for key in keys]
            lengths = [dist(a, b) / scale, dist(b, c) / scale]
            plausibility[side + limb.title()] = {
                "segmentLengthsHeight": lengths,
                "segmentRatio": lengths[0] / lengths[1] if lengths[1] else None,
            }
    for suffix in ("Shoulder", "Elbow", "Hand", "Hip", "Knee", "Foot"):
        a = result["views"]["front"]["geometry"]["positions"]["left" + suffix]
        b = result["views"]["front"]["geometry"]["positions"]["right" + suffix]
        depths = result["views"]["side"]["geometry"]["positions"]
        plausibility[suffix + "Symmetry"] = {
            "sideSumHeight": a["horizontalN"] + b["horizontalN"],
            "heightDifference": a["height01"] - b["height01"],
            "depthDifference": depths["left" + suffix]["horizontalN"]
            - depths["right" + suffix]["horizontalN"],
        }
    result["plausibility"] = plausibility


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
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--check-duplicates", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    baseline = (
        json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
    )
    guides = json.loads(args.guides.read_text(encoding="utf-8")) if args.guides else {}
    blob = GeometryBlob()
    payload = load_mod(str(args.mod), geometry=blob)
    if payload.get("error"):
        raise RuntimeError(payload["error"])
    payload["_fixture_blob"] = bytes(blob.data)
    browser_fixture = edge_browser.__wrapped__()
    browser = next(browser_fixture)
    viewer_fixture = viewer.__wrapped__(browser, frontend_url.__wrapped__())
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
          for (const mesh of window.modViewer.activeMeshes)
            if (patterns.some(pattern=>mesh.userData.semanticKey.includes(pattern))) mesh.visible=false;
        }""",
            args.hide,
        )
        page.evaluate("""async () => {
          window.__evaluationRigApi=(await import('./js/weight-rig/weight-rig-core.js')).weightRigApi;
          await window.__evaluationRigApi.previewGeometryRig();
        }""")
        result = page.evaluate("""async () => {
          const rig=window.__evaluationRigApi.getModelRigState().geometryRigPreview?.rig;
          if (!rig?.available) throw new Error('No fitted geometry rig');
          const {isHumanoidMeshDisplayed}=await import('./js/weight-rig/humanoid-geometry-fit.js');
          const meshes=window.modViewer.activeMeshes;
          return {schemaVersion:2,frame:rig.frame,diagnostics:rig.diagnostics,
            confidenceDefinition:'Available anatomical surface evidence, not verified joint accuracy',
            controls:Object.fromEntries([['geometry',rig],['proportional',rig.proportionalRig]].map(([mode,r])=>
              [mode,Object.fromEntries(Object.entries(r.controls).map(([key,c])=>[key,c.position]))])),
            inactiveMeshes:meshes.filter(mesh=>!isHumanoidMeshDisplayed(mesh)).length,
            displayedTriangles:meshes.filter(isHumanoidMeshDisplayed).reduce((sum,mesh)=>{
              const count=mesh.geometry.index?.count ?? mesh.geometry.attributes.position.count;
              return sum+Math.floor(Math.max(0,Math.min(count-mesh.geometry.drawRange.start,
                mesh.geometry.drawRange.count))/3);
            },0),weightRequests:window.__bridge.calls.filter(call=>call.name==='weights').length};
        }""")
        if args.check_duplicates:
            result["duplicateCheck"] = page.evaluate("""async () => {
              const THREE=await import('three');
              const {fitHumanoidGeometryRig,isHumanoidMeshDisplayed}=await import('./js/weight-rig/humanoid-geometry-fit.js');
              const {getModelTransformState}=await import('./js/scene/scene.js');
              const meshes=window.modViewer.activeMeshes;
              const duplicates=meshes.filter(isHumanoidMeshDisplayed).map(mesh=>{
                const duplicate=new THREE.Mesh(mesh.geometry.clone(),mesh.material);
                duplicate.position.copy(mesh.position);duplicate.quaternion.copy(mesh.quaternion);duplicate.scale.copy(mesh.scale);
                duplicate.userData.humanoidRestPositions=new Float32Array(mesh.userData.humanoidRestPositions);
                return duplicate;
              });
              const fit=await fitHumanoidGeometryRig({meshes:[...meshes,...duplicates],orientationState:getModelTransformState()});
              const original=window.__evaluationRigApi.getModelRigState().geometryRigPreview.rig;
              const deviation=Math.max(...Object.keys(original.controls).map(key=>Math.hypot(...
                original.controls[key].position.map((v,i)=>v-fit.controls[key].position[i]))/original.frame.height));
              duplicates.forEach(mesh=>mesh.geometry.dispose());
              return {maxDeviationHeight:deviation};
            }""")
        result["views"] = {}
        for view in ("front", "side"):
            result["views"][view] = {}
            for mode in (
                "proportional",
                "geometry",
                *(["iteration1"] if baseline else []),
            ):
                capture = page.evaluate(
                    CAPTURE_VIEW,
                    {
                        "view": view,
                        "mode": mode,
                        "baseline": baseline["controls"]["geometry"]
                        if mode == "iteration1"
                        else None,
                    },
                )
                page.locator("#canvas-container").screenshot(
                    path=str(args.output / f"{view}-{mode}.png")
                )
                page.evaluate("window.__evaluationRestore()")
                result["views"][view][mode] = capture
        add_comparison(result, guides, baseline)
        (args.output / "comparison.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "controls": len(result["controls"]["geometry"]),
                    "weightRequests": result["weightRequests"],
                    "fitRuntimeMs": result["diagnostics"]["fitRuntimeMs"],
                    "surfaceSamples": result["diagnostics"]["sampledPointCount"],
                    "duplicateDeviation": result.get("duplicateCheck", {}).get(
                        "maxDeviationHeight"
                    ),
                    "summary": result["summary"],
                }
            )
        )
    finally:
        for fixture in (viewer_fixture, browser_fixture):
            try:
                next(fixture)
            except StopIteration:
                pass


if __name__ == "__main__":
    main()
