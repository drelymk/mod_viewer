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


CAPTURE_VIEW = """async ({view, mode, baseline, preservePose=false, referenceFrame=null}) => {
  const THREE=await import('three');
  const {camera,renderer,scene,controls}=await import('./js/scene/scene.js');
  const {weightRigApi}=await import('./js/weight-rig/weight-rig-core.js');
  const {semanticToHumanoidControlPosition,rebuildHumanoidControlPaths}=
    await import('./js/weight-rig/humanoid-control-rig.js');
  if (!preservePose) weightRigApi.setGeometryRigPreviewMode(mode==='proportional'?'proportional':'geometry');
  const rig=weightRigApi.getModelRigState().geometryRigPreview.rig;
  const frameRig=referenceFrame?{frame:referenceFrame}:rig;
  const saved=structuredClone(rig.controls);
  if (baseline) {
    for (const [key,control] of Object.entries(rig.controls)) control.position=[...baseline[key]];
    rebuildHumanoidControlPaths(rig);weightRigApi.setGeometryRigPreviewMode('geometry');
  }
  const mesh=window.modViewer.activeMeshes.find(mesh=>mesh.visible);
  mesh.updateWorldMatrix(true,false);
  const matrix=mesh.matrixWorld;
  const rect=renderer.domElement.getBoundingClientRect();
  const point=semantic=>new THREE.Vector3(...semanticToHumanoidControlPosition(semantic,frameRig)).applyMatrix4(matrix);
  const bottom=point({sideN:0,height01:0,depthN:0});
  const top=point({sideN:0,height01:1,depthN:0});
  const height=top.distanceTo(bottom);
  const center=top.clone().add(bottom).multiplyScalar(0.5);
  const up=new THREE.Vector3(...frameRig.frame.up).transformDirection(matrix);
  const direction=new THREE.Vector3(...(view==='front'?frameRig.frame.forward:frameRig.frame.right.map(v=>-v)))
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
  const projectedBottom=project(semanticToHumanoidControlPosition({sideN:0,height01:0,depthN:0},frameRig));
  const projectedTop=project(semanticToHumanoidControlPosition({sideN:0,height01:1,depthN:0},frameRig));
  const pixels=Math.abs(projectedBottom[1]-projectedTop[1]);
  const positions=Object.fromEntries(Object.entries(rig.controls).map(([key,c])=>{
    const p=project(weightRigApi.getModelRigState().geometryRigPreview.pose?.positions[key] || c.position);
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
            direction = [last - first for first, last in zip(a, c)]
            offset = [joint - first for first, joint in zip(a, b)]
            squared = sum(value * value for value in direction)
            along = (
                sum(x * y for x, y in zip(direction, offset)) / squared
                if squared
                else 0
            )
            aligned = [first + along * delta for first, delta in zip(a, direction)]
            plausibility[side + limb.title()] = {
                "segmentLengthsHeight": lengths,
                "segmentRatio": lengths[0] / lengths[1] if lengths[1] else None,
                "jointLineDistanceHeight": dist(b, aligned) / scale,
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


PREVIEW_UPDATE_COST = """() => {
  const api=window.__evaluationRigApi;
  api.setGeometryRigPreviewIkEnabled(true);
  const rig=api.getModelRigState().geometryRigPreview.rig;
  const status=document.querySelector('.rig-preview-test-ik').parentElement.nextElementSibling;
  const descriptor=Object.getOwnPropertyDescriptor(Node.prototype,'textContent');
  let writes=0;
  Object.defineProperty(status,'textContent',{configurable:true,
    get(){return descriptor.get.call(this);},set(value){writes++;descriptor.set.call(this,value);}});
  const times=[];
  try {
    for(let i=0;i<210;i++) {
      const key=['leftHand','rightHand','leftFoot','rightFoot'][i%4];
      const target=rig.controls[key].position.map((v,j)=>v+rig.frame.height*
        (rig.frame.up[j]*0.12+rig.frame.forward[j]*(i%2?0.12:-0.12)));
      const start=performance.now();api.solveGeometryRigPreviewIk(key,target);
      if(i>=10) times.push(performance.now()-start);
      else writes=0;
    }
    times.sort((a,b)=>a-b);
    return {solves:times.length,poseStatusWritesPerSolve:writes/times.length,
      medianUpdateMs:times[Math.floor(times.length/2)],p95UpdateMs:times[Math.floor(times.length*.95)]};
  } finally {delete status.textContent;api.setGeometryRigPreviewIkEnabled(false);}
}"""


POSE_CASE = """async ({name, displacements}) => {
  const api=window.__evaluationRigApi;
  api.resetGeometryRigPreviewPose();
  if (window.__evaluationKeepGeometryPlanes) {
    const pose=api.getModelRigState().geometryRigPreview.pose;
    for (const [role,limb] of Object.entries(pose.limbs))
      limb.bendDirection=[...window.__evaluationGeometryPlanes[role].bendDirection];
  }
  const rig=api.getModelRigState().geometryRigPreview.rig, height=rig.frame.height;
  const {HUMANOID_LIMB_CONTROLS}=await import('./js/weight-rig/humanoid-rig-ik.js');
  const distance=(a,b)=>Math.hypot(...a.map((v,i)=>v-b[i]));
  const dot=(a,b)=>a.reduce((sum,v,i)=>sum+v*b[i],0);
  const result={};
  for (const [role,keys] of Object.entries(HUMANOID_LIMB_CONTROLS)) {
    const rest=keys.map(key=>rig.controls[key].position);
    const lengths=[distance(rest[0],rest[1]),distance(rest[1],rest[2])];
    const delta=displacements[role][name];
    const target=rest[2].map((v,i)=>v+height*(rig.frame.right[i]*delta[0]+rig.frame.up[i]*delta[1]+rig.frame.forward[i]*delta[2]));
    const before=structuredClone(api.getModelRigState().geometryRigPreview.pose.positions);
    let previous=rest[1], previousTarget=rest[2], previousPerpendicular=null;
    const row={maxSegmentErrorHeight:0,maxAnchorShiftHeight:0,maxOtherLimbShiftHeight:0,
      maxStepBendHeight:0,maxStepTargetHeight:0,minBendAlignment:1,minStepBendAlignment:1,
      maxReturnStepBendHeight:0,bendSignChanges:0,finite:true};
    const sample=(at,returning=false)=>{
      api.solveGeometryRigPreviewIk(keys[2],at);
      const pose=api.getModelRigState().geometryRigPreview.pose;
      const points=keys.map(key=>pose.positions[key]||rig.controls[key].position);
      row.maxSegmentErrorHeight=Math.max(row.maxSegmentErrorHeight,
        Math.abs(distance(points[0],points[1])-lengths[0])/height,
        Math.abs(distance(points[1],points[2])-lengths[1])/height);
      row.maxAnchorShiftHeight=Math.max(row.maxAnchorShiftHeight,distance(points[0],rest[0])/height);
      row.maxOtherLimbShiftHeight=Math.max(row.maxOtherLimbShiftHeight,0,...Object.keys(before)
        .filter(key=>!keys.includes(key)).map(key=>distance(before[key],pose.positions[key])/height));
      row.finite &&= points.flat().every(Number.isFinite);
      row.maxStepBendHeight=Math.max(row.maxStepBendHeight,distance(previous,points[1])/height);
      if(returning) row.maxReturnStepBendHeight=Math.max(row.maxReturnStepBendHeight,distance(previous,points[1])/height);
      row.maxStepTargetHeight=Math.max(row.maxStepTargetHeight,distance(previousTarget,at)/height);
      const axis=points[2].map((v,i)=>v-points[0][i]);const axisLength=Math.hypot(...axis);
      const unit=axis.map(v=>v/axisLength), offset=points[1].map((v,i)=>v-points[0][i]);
      const perpendicular=offset.map((v,i)=>v-unit[i]*dot(offset,unit));
      const size=Math.hypot(...perpendicular), frozen=pose.limbs[role].bendDirection;
      const projected=frozen.map((v,i)=>v-unit[i]*dot(frozen,unit));
      const projectedSize=Math.hypot(...projected);
      if (size>height*1e-6 && projectedSize>1e-6) {
        const normal=perpendicular.map(v=>v/size);
        // The preferred plane is fully applied outside the rest-return region.
        if(distance(at,rest[2])>=pose.limbs[role].restTransitionDistance)
          row.minBendAlignment=Math.min(row.minBendAlignment,dot(normal,projected)/projectedSize);
        if (previousPerpendicular) {
          const alignment=dot(previousPerpendicular,normal);
          row.minStepBendAlignment=Math.min(row.minStepBendAlignment,alignment);
          if(alignment<0) row.bendSignChanges++;
        }
        previousPerpendicular=normal;
      }
      previous=points[1];previousTarget=at;
    };
    for (let step=1;step<=40;step++) sample(rest[2].map((v,i)=>v+(target[i]-v)*step/40));
    const pose=api.getModelRigState().geometryRigPreview.pose;
    const state=pose.limbs[role];
    const end=pose.positions[keys[2]], bend=pose.positions[keys[1]];
    const axis=end.map((v,i)=>v-rest[0][i]), offset=bend.map((v,i)=>v-rest[0][i]);
    const along=dot(offset,axis)/dot(axis,axis);
    row.bendOffsetForwardHeight=dot(offset.map((v,i)=>v-axis[i]*along),rig.frame.forward)/height;
    Object.assign(row,{reached:state.reached,residualHeight:state.residual/height,
      segmentLengthsHeight:lengths.map(v=>v/height)});
    for (let step=39;step>=0;step--) sample(rest[2].map((v,i)=>v+(target[i]-v)*step/40),true);
    row.restTargetJointShiftHeight=distance(api.getModelRigState().geometryRigPreview.pose.positions[keys[1]],rest[1])/height;
    api.solveGeometryRigPreviewIk(keys[2],target);
    result[role]=row;
  }
  return result;
}"""


def evaluate_poses(page, output, guides):
    """Use the preview API; reference edits live only in this isolated fixture."""
    displacements = page.evaluate("""async () => {
      const rest=window.__evaluationRigApi.getModelRigState().geometryRigPreview.rig;
      const {HUMANOID_LIMB_CONTROLS}=await import('./js/weight-rig/humanoid-rig-ik.js');
      return Object.fromEntries(Object.entries(HUMANOID_LIMB_CONTROLS).map(([role,keys])=>{
        const first=rest.controls[keys[0]].position,last=rest.controls[keys[2]].position;
        const direction=last.map((v,i)=>v-first[i]),length=Math.hypot(...direction);
        const radial=axis=>direction.reduce((sum,v,i)=>sum+v*axis[i],0)/length*0.25;
        const inward=role.startsWith('left')?0.04:-0.04;
        return [role,{toward:[inward,0.12,0.12],away:[inward,0.12,-0.12],
          extension:[radial(rest.frame.right),radial(rest.frame.up),radial(rest.frame.forward)]}];
      }));
    }""")
    report = {
        "targetDisplacementsHeight": displacements,
        "dragUpdates": page.evaluate(PREVIEW_UPDATE_COST),
        "modes": {},
    }
    corrected = {}
    for key, entry in guides.items():
        guide = entry.get("front", {})
        if guide.get("kind") not in ("visible", "estimated"):
            continue
        if not all(axis in guide for axis in ("horizontalN", "height01")):
            continue
        corrected[key] = {
            "side": guide["horizontalN"],
            "height": guide["height01"],
        }
        side = entry.get("side", {})
        if side.get("kind") == "visible" and "horizontalN" in side:
            corrected[key]["depth"] = side["horizontalN"]
    for mode in (
        "proportional",
        "geometry",
        *(["reference", "reference-fixed-plane"] if corrected else []),
    ):
        page.evaluate(
            """async ({mode,corrected}) => {
          const api=window.__evaluationRigApi;
          api.setGeometryRigPreviewMode(mode==='proportional'?'proportional':'geometry');
          const rig=api.getModelRigState().geometryRigPreview.rig;
          window.__evaluationOriginalControls=rig.controls;
          if (mode.startsWith('reference')) {
            rig.controls=structuredClone(rig.controls);
            for (const [key,value] of Object.entries(corrected)) {
              const p=rig.controls[key].position, frame=rig.frame;
              const side=p.reduce((sum,v,i)=>sum+v*frame.right[i],0);
              const up=p.reduce((sum,v,i)=>sum+v*frame.up[i],0);
              const depth=p.reduce((sum,v,i)=>sum+v*frame.forward[i],0);
              rig.controls[key].position=p.map((v,i)=>v+frame.right[i]*(frame.sideCenter+value.side*frame.height-side)
                +frame.up[i]*(frame.lowHeight+value.height*frame.height-up)
                +(value.depth===undefined?0:frame.forward[i]*(frame.depthCenter+value.depth*frame.height-depth)));
            }
            const {rebuildHumanoidControlPaths}=await import('./js/weight-rig/humanoid-control-rig.js');
            rebuildHumanoidControlPaths(rig);api.setGeometryRigPreviewMode('geometry');
          }
          api.setGeometryRigPreviewIkEnabled(true);
          if (mode==='geometry') window.__evaluationGeometryPlanes=structuredClone(api.getModelRigState().geometryRigPreview.pose.limbs);
          window.__evaluationKeepGeometryPlanes=mode==='reference-fixed-plane';
        }""",
            {"mode": mode, "corrected": corrected},
        )
        mode_report = {}
        for movement in ("toward", "away", "extension"):
            mode_report[movement] = page.evaluate(
                POSE_CASE, {"name": movement, "displacements": displacements}
            )
            if (
                mode == "reference-fixed-plane"
                or movement == "extension"
                or (mode == "reference" and movement == "away")
            ):
                continue
            for view in ("front", "side") if movement == "toward" else ("side",):
                page.evaluate(
                    CAPTURE_VIEW,
                    {"view": view, "mode": "geometry", "preservePose": True},
                )
                page.locator("#canvas-container").screenshot(
                    path=str(output / f"pose-{mode}-{movement}-{view}.png")
                )
                page.evaluate("window.__evaluationRestore()")
        report["modes"][mode] = mode_report
        page.evaluate("""async () => {
          const api=window.__evaluationRigApi,rig=api.getModelRigState().geometryRigPreview.rig;
          rig.controls=window.__evaluationOriginalControls;
          const {rebuildHumanoidControlPaths}=await import('./js/weight-rig/humanoid-control-rig.js');
          rebuildHumanoidControlPaths(rig);api.setGeometryRigPreviewIkEnabled(false);
        }""")
    report["referenceMethod"] = (
        "Temporary frontal visible/estimated guide corrections and visible side corrections; other depths retained. "
        "Estimated leg pivots are diagnostic hypotheses, not ground truth."
    )
    return report


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
    parser.add_argument(
        "--poses", action="store_true", help="Evaluate skeleton-only endpoint motions"
    )
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
        result["meshCounts"] = {
            "displayed": result["diagnostics"]["visibleMeshCount"],
            "inactive": result["inactiveMeshes"],
            "total": result["diagnostics"]["visibleMeshCount"]
            + result["inactiveMeshes"],
        }
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
        if args.poses:
            page.evaluate(
                "window.__evaluationRigApi.setGeometryRigPreviewMode('geometry')"
            )
            before = page.evaluate("""() => ({rest:JSON.stringify(window.__evaluationRigApi.getModelRigState().geometryRigPreview.rig),
              geometry:window.modViewer.activeMeshes.map(mesh=>[...mesh.geometry.attributes.position.array])})""")
            result["posing"] = evaluate_poses(page, args.output, guides)
            result["posing"].update(
                page.evaluate(
                    """before => ({
              restUnchanged:JSON.stringify(window.__evaluationRigApi.getModelRigState().geometryRigPreview.rig)===before.rest,
              modelGeometryUnchanged:window.modViewer.activeMeshes.every((mesh,i)=>
                mesh.geometry.attributes.position.array.every((v,j)=>v===before.geometry[i][j])),
              weightRequests:window.__bridge.calls.filter(call=>call.name==='weights').length,
              modelRigLoaded:window.__evaluationRigApi.getModelRigState().loaded})""",
                    before,
                )
            )
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
