"""Check fine hand drags and one-arm deformation in the local viewer.

Requires a compatible browser/WebGPU runtime. Deformation is opt-in and uses
existing authored influences and binding/deformation helpers in an isolated
evaluation session. It never exports edits or saves poses.
"""

import argparse
import json
from math import dist
from pathlib import Path
import sys

from evaluate_geometry_rig import CAPTURE_VIEW


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


INTERACTION_SETUP = """async view => {
  window.__armApi=window.__evaluationRigApi;
  window.__armScene=await import('./js/scene/scene.js');
  const THREE=await import('three');
  const {projectRigPointToClient}=await import('./js/scene/rig-overlay-controller.js');
  const {camera,controls,scene,renderer}=__armScene;
  const rig=__armApi.getModelRigState().geometryRigPreview.rig;
  const overlay=scene.getObjectByName('viewer-inferred-rig-overlay');
  overlay.updateMatrixWorld(true);
  if(view==='oblique') {
    const direction=new THREE.Vector3(...rig.frame.forward).transformDirection(overlay.matrixWorld);
    const right=new THREE.Vector3(...rig.frame.right).transformDirection(overlay.matrixWorld);
    camera.position.copy(controls.target).addScaledVector(direction.addScaledVector(right,.8).normalize(),rig.frame.height*4);
    camera.lookAt(controls.target);camera.updateMatrixWorld(true);
  }
  controls.enabled=true;
  window.__armPoint=key=>{
    const state=__armApi.getModelRigState().geometryRigPreview;
    return projectRigPointToClient({point:state.pose?.positions[key]||rig.controls[key].position,
      camera,canvas:renderer.domElement,worldMatrix:overlay.matrixWorld});
  };
  window.__armSample=async key=>{
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    await renderer.renderAsync(scene,camera);
    const pose=__armApi.getModelRigState().geometryRigPreview.pose;
    const bendKey=key.replace('Hand','Elbow'), anchorKey=key.replace('Hand','Shoulder');
    const end=pose.positions[key]||rig.controls[key].position;
    const bend=pose.positions[bendKey]||rig.controls[bendKey].position;
    const anchor=new THREE.Vector3(...rig.controls[anchorKey].position);
    const axis=new THREE.Vector3(...end).sub(anchor).normalize();
    const plane=new THREE.Vector3(...bend).sub(anchor);
    plane.addScaledVector(axis,-plane.dot(axis));
    const limb=pose.limbs[key==='leftHand'?'left_arm':'right_arm'];
    const restPlane=limb.restBendDirection&&new THREE.Vector3(...limb.restBendDirection);
    if(restPlane) restPlane.addScaledVector(axis,-restPlane.dot(axis)).normalize();
    const turn=restPlane&&plane.length()>rig.frame.height*1e-5
      ?Math.acos(THREE.MathUtils.clamp(plane.normalize().dot(restPlane),-1,1))*180/Math.PI:0;
    return {selected:pose.selectedControlKey,end,bend,bendPlaneTurnDegrees:turn};
  };
  return {height:rig.frame.height,controls:Object.fromEntries(Object.entries(rig.controls).map(([k,v])=>[k,v.position]))};
}"""


def evaluate_near_rest(page, output):
    """Drive pointer gestures rather than calling the solver directly."""
    rows = []
    for view in ("front", "oblique"):
        capture = page.evaluate(
            CAPTURE_VIEW, {"view": "front", "mode": "geometry", "baseline": None}
        )
        rest = page.evaluate(INTERACTION_SETUP, view)
        height = rest["height"]
        for key in ("leftHand", "rightHand"):
            hand = page.evaluate("__armPoint", key)
            shoulder = page.evaluate("__armPoint", key.replace("Hand", "Shoulder"))
            inward = 1 if hand["x"] < shoulder["x"] else -1
            for flipped in (False, True):
                for direction, dx, dy in (
                    ("up", 0, -1),
                    ("down", 0, 1),
                    ("inward", inward, 0),
                    ("outward", -inward, 0),
                ):
                    page.evaluate("""() => {
                      __armApi.setGeometryRigPreviewIkEnabled(true);
                      __armApi.resetGeometryRigPreviewPose();
                    }""")
                    origin = page.evaluate("__armPoint", key)
                    page.mouse.move(origin["x"], origin["y"])
                    page.mouse.down()
                    page.mouse.up()
                    selected = page.evaluate(
                        "__armApi.getModelRigState().geometryRigPreview.pose.selectedControlKey"
                    )
                    if selected != key:
                        raise RuntimeError("The intended hand was not selected")
                    if flipped:
                        page.locator(".rig-preview-flip-bend").click()
                    page.mouse.move(origin["x"], origin["y"])
                    page.mouse.down()
                    if page.evaluate("__armScene.controls.enabled"):
                        raise RuntimeError(
                            "The hand drag did not acquire camera control"
                        )
                    samples = [page.evaluate("__armSample", key)]
                    representative = (
                        view == "front" and key == "leftHand" and direction == "inward"
                    )
                    if representative:
                        page.locator("#canvas-container").screenshot(
                            path=str(output / f"fine-hand-{flipped}-0.png")
                        )
                    for step in [*range(1, 21), *range(19, -1, -1)]:
                        offset = capture["heightPixels"] * 0.02 * step / 20
                        page.mouse.move(
                            origin["x"] + dx * offset, origin["y"] + dy * offset
                        )
                        samples.append(page.evaluate("__armSample", key))
                        if representative and len(samples) <= 21 and step % 5 == 0:
                            page.locator("#canvas-container").screenshot(
                                path=str(output / f"fine-hand-{flipped}-{step}.png")
                            )
                    page.mouse.up()
                    if not page.evaluate("__armScene.controls.enabled"):
                        raise RuntimeError(
                            "The hand drag did not restore camera control"
                        )
                    if any(sample["selected"] != key for sample in samples):
                        raise RuntimeError(
                            "The hand drag changed the selected endpoint"
                        )
                    bend_key = key.replace("Hand", "Elbow")
                    anchor = rest["controls"][key.replace("Hand", "Shoulder")]
                    rest_bend, rest_end = (
                        rest["controls"][bend_key],
                        rest["controls"][key],
                    )
                    lengths = dist(anchor, rest_bend), dist(rest_bend, rest_end)
                    rows.append(
                        {
                            "view": view,
                            "endpoint": key,
                            "flipped": flipped,
                            "direction": direction,
                            "maxHandShiftHeight": max(
                                dist(s["end"], rest_end) / height for s in samples
                            ),
                            "maxElbowShiftHeight": max(
                                dist(s["bend"], rest_bend) / height for s in samples
                            ),
                            "maxStepElbowHeight": max(
                                dist(a["bend"], b["bend"]) / height
                                for a, b in zip(samples, samples[1:])
                            ),
                            "maxBendPlaneTurnDegrees": max(
                                s["bendPlaneTurnDegrees"] for s in samples
                            ),
                            "restReturnHeight": dist(samples[-1]["bend"], rest_bend)
                            / height,
                            "maxSegmentErrorHeight": max(
                                max(
                                    abs(dist(anchor, s["bend"]) - lengths[0]),
                                    abs(dist(s["bend"], s["end"]) - lengths[1]),
                                )
                                / height
                                for s in samples
                            ),
                        }
                    )
    page.evaluate("__armApi.setGeometryRigPreviewIkEnabled(false)")
    return {"paths": rows, "stepsPerDirection": 20, "pointerDisplacementHeight": 0.02}


def authored_skin_payload(context):
    """Decode the retained render compaction through the shared source rules."""
    from core.geometry.buffers import BufferStore
    from core.geometry.skinning import decode_skinning, skinning_source_descriptor

    buffers = BufferStore(source=context.source)
    meshes, pieces, offset = {}, [], 0
    for key, manifest in sorted(context.skinning_manifest.items()):
        source = manifest.skinning_source
        raw = buffers.raw(context.source.resolve_resource(source.file))
        remap = (
            buffers.raw(context.source.resolve_resource(source.vertex_vg_file))
            if source.vertex_vg_file
            else None
        )
        decoded = decode_skinning(source, raw, manifest.used_vertices, remap)
        if decoded.vertex_count != manifest.vertex_count or any(
            decoded.diagnostics[name]
            for name in ("truncated_vertices", "vertex_vg_truncated_vertices")
        ):
            raise RuntimeError("Authored influences do not match rendered compaction")
        meshes[key] = {
            "status": "ok",
            "vertex_count": decoded.vertex_count,
            "influence_count": decoded.influence_count,
            "bone_ids": list(decoded.bone_ids),
            "encoding": source.encoding,
            "source": skinning_source_descriptor(source),
            "diagnostics": decoded.diagnostics,
            "weight_stats": {str(k): v for k, v in decoded.bone_stats.items()},
            "data": {
                "indices": {
                    "offset": offset,
                    "length": len(decoded.indices),
                    "type": "u32",
                },
                "weights": {
                    "offset": offset + len(decoded.indices),
                    "length": len(decoded.weights),
                    "type": "f32",
                },
            },
        }
        piece = decoded.indices + decoded.weights
        pieces.append(piece)
        offset += len(piece)
    return meshes, b"".join(pieces)


DEFORMATION_SETUP = """async ({entries,url}) => {
  const THREE=await import('three');
  const {createWeightRuntimeState}=await import('./js/weight-rig/weight-runtime.js');
  const {createSkinningRuntime}=await import('./js/weight-rig/skinning-runtime.js');
  const {createRigSourceSession}=await import('./js/weight-rig/rig-model-session.js');
  const {rebuildSourceRigRestFrames,rebuildModelRestFrames}=await import('./js/weight-rig/weight-rig-frames.js');
  const {buildModelRigReconciliationCooperative,sourceBoneKey}=await import('./js/weight-rig/weight-rig-reconcile.js');
  const {buildHumanoidControlRig}=await import('./js/weight-rig/humanoid-control-rig.js');
  const {characterAxesFromOrientation}=await import('./js/weight-rig/humanoid-orientation.js');
  const {buildHumanoidRigBinding,buildHumanoidDriverBaseTransforms}=await import('./js/weight-rig/humanoid-rig-binding.js');
  const {applyWeightedTransformDeformationInto,applyWeightedNormalDeformationInto}=await import('./js/weight-rig/weight-deformation.js');
  const {getModelTransformState}=await import('./js/scene/scene.js');
  const geometry=__evaluationRigApi.getModelRigState().geometryRigPreview.rig;
  const geometryIdentity=JSON.stringify(geometry);
  const runtime=createWeightRuntimeState();
  const skin=createSkinningRuntime({...runtime,requestRender:()=>{}});
  const buffer=await (await fetch(url)).arrayBuffer();
  const meshes=window.modViewer.activeMeshes;
  for(const mesh of meshes) {
    runtime.knownMeshes.add(mesh);
    const entry=entries[mesh.userData.semanticKey];
    if(!entry) continue;
    const {source}=skin.installSkinningEntry(mesh,entry,buffer);
    runtime.modelWeightState.sourceDescriptors.set(source.sourceKey,source);
  }
  const session=createRigSourceSession({...runtime,sourceSkinningRigs:new Map(),
    ensureRigMeshPrepared:skin.ensureRigMeshPrepared,
    ensureInfluenceGraphCooperative:skin.ensureInfluenceGraphCooperative,
    rebuildRestFrames:rebuildSourceRigRestFrames,cloneForest:structuredClone});
  const sources=await session.buildAllCooperative();
  const reconciled=await buildModelRigReconciliationCooperative(sources);
  const model={joints:reconciled.joints,components:reconciled.components,
    inferredForest:{components:reconciled.components},centerByJointId:reconciled.centerByJointId,
    jointPivotByJointId:reconciled.jointPivotByJointId,restFrameByJointId:reconciled.restFrameByJointId,
    jointPivotByEdgeKey:reconciled.jointPivotByEdgeKey,componentByJointId:reconciled.componentByJointId};
  rebuildModelRestFrames(model,model.inferredForest);
  const orientationState=getModelTransformState();
  const current=buildHumanoidControlRig({meshes,axes:characterAxesFromOrientation(orientationState),orientationState});
  if(!current.accepted) throw new Error('The current automatic humanoid Rig is unavailable');
  current.available=true;
  const controls={geometry,current};
  const bindings=Object.fromEntries(Object.entries(controls).map(([name,controlRig])=>{
    const binding=buildHumanoidRigBinding({controlRig,modelRig:model});
    binding.jointBindings=new Map([...binding.jointBindings].filter(([,v])=>
      ['left_upper_arm','left_lower_arm'].includes(v.driverId)));
    return [name,binding];
  }));
  const baselines=meshes.map(mesh=>({positions:new Float32Array(mesh.geometry.attributes.position.array),
    normals:mesh.geometry.attributes.normal&&new Float32Array(mesh.geometry.attributes.normal.array)}));
  const originalWeights=[...runtime.knownMeshes].filter(m=>runtime.stateFor(m).loaded).map(m=>{
    const state=runtime.stateFor(m);return {state,indices:new Uint32Array(state.indices),weights:new Float32Array(state.weights)};
  });
  window.__armDeform={THREE,geometry,geometryIdentity,controls,bindings,model,reconciled,runtime,meshes,baselines,originalWeights,
    buildHumanoidDriverBaseTransforms,applyWeightedTransformDeformationInto,applyWeightedNormalDeformationInto,sourceBoneKey};
  return {sourceCount:sources.length,jointCount:model.joints.length,
    authoredMeshCount:originalWeights.length,boundArmJoints:Object.fromEntries(Object.entries(bindings).map(([k,v])=>[k,v.jointBindings.size])),
    geometryIndependent:JSON.stringify(geometry)===geometryIdentity,
    currentControlSource:current.mode||current.source||'automatic_humanoid',
    referenceFrame:geometry.frame,
    controls:Object.fromEntries(Object.entries(controls).map(([name,r])=>[name,Object.fromEntries(Object.entries(r.controls).map(([k,v])=>[k,v.position]))]))};
}"""


DEFORM_POSE = """async ({mode,positions}) => {
  const d=__armDeform, rig=d.controls[mode], binding=d.bindings[mode];
  const height=d.geometry.frame.height;
  const {result}=d.buildHumanoidDriverBaseTransforms({binding,controlRig:rig,modelRig:d.model,posedControls:positions});
  let movedVertices=0,maxDisplacementHeight=0,unaffectedShiftHeight=0,finite=true;
  const edgeRatios=[],meshRows=[];
  for(let meshIndex=0;meshIndex<d.meshes.length;meshIndex++) {
    const mesh=d.meshes[meshIndex], base=d.baselines[meshIndex], state=d.runtime.stateFor(mesh);
    const position=mesh.geometry.attributes.position;
    position.array.set(base.positions);
    const normal=mesh.geometry.attributes.normal;
    if(normal&&base.normals) normal.array.set(base.normals);
    if(state.loaded&&Object.keys(positions).length) {
      const transforms=new Map(), rotations=new Map();
      for(const boneId of state.boneIds) {
        const jointId=d.reconciled.sourceBoneToModelJointMap.get(d.sourceBoneKey(state.skinningSourceKey,boneId));
        const matrix=result.get(jointId);
        if(matrix) {transforms.set(boneId,matrix);rotations.set(boneId,new d.THREE.Quaternion().setFromRotationMatrix(matrix).normalize());}
      }
      d.applyWeightedTransformDeformationInto(position.array,base.positions,state.indices,state.weights,state.influenceCount,transforms);
      if(normal&&base.normals) d.applyWeightedNormalDeformationInto(normal.array,base.normals,state.indices,state.weights,state.influenceCount,rotations);
      for(let vertex=0;vertex<position.count;vertex++) {
        const at=vertex*3, delta=Math.hypot(...[0,1,2].map(i=>position.array[at+i]-base.positions[at+i]))/height;
        const affected=Array.from({length:state.influenceCount},(_,i)=>vertex*state.influenceCount+i)
          .some(i=>state.weights[i]>0&&transforms.has(state.indices[i]));
        if(!affected) unaffectedShiftHeight=Math.max(unaffectedShiftHeight,delta);
        if(delta>1e-6) movedVertices++;
        maxDisplacementHeight=Math.max(maxDisplacementHeight,delta);
      }
    }
    finite&&=position.array.every(Number.isFinite)&&(!normal||normal.array.every(Number.isFinite));
    if(mesh.visible&&Object.keys(positions).length) {
      const seen=new Set(), indices=mesh.geometry.index?.array;
      const count=indices?.length||position.count;
      const start=mesh.geometry.drawRange.start, end=Math.min(count,start+mesh.geometry.drawRange.count);
      let moved=0;
      for(let vertex=0;vertex<position.count;vertex++) {
        const delta=Math.hypot(...[0,1,2].map(i=>position.array[vertex*3+i]-base.positions[vertex*3+i]))/height;
        if(delta>1e-6) moved++;
      }
      meshRows.push({mesh:mesh.userData.semanticKey,movedVertices:moved});
      for(let triangle=start;triangle+2<end;triangle+=3) {
        const vertices=[0,1,2].map(i=>indices?indices[triangle+i]:triangle+i);
        for(const [a,b] of [[vertices[0],vertices[1]],[vertices[1],vertices[2]],[vertices[2],vertices[0]]]) {
          const edge=a<b?`${a}:${b}`:`${b}:${a}`;
          if(seen.has(edge)) continue;seen.add(edge);
          const length=values=>Math.hypot(...[0,1,2].map(i=>values[a*3+i]-values[b*3+i]));
          const rest=length(base.positions), posed=length(position.array);
          const shifted=[a,b].some(v=>[0,1,2].some(i=>Math.abs(position.array[v*3+i]-base.positions[v*3+i])>height*1e-6));
          if(shifted&&rest>height*1e-5) edgeRatios.push(posed/rest);
        }
      }
    }
    position.needsUpdate=true;if(normal) normal.needsUpdate=true;
    mesh.geometry.computeBoundingBox();mesh.geometry.computeBoundingSphere();
  }
  edgeRatios.sort((a,b)=>a-b);
  const {invalidateCharacterShadowGeometry}=await import('./js/scene/scene.js');
  invalidateCharacterShadowGeometry();
  return {movedVertices,maxDisplacementHeight,unaffectedShiftHeight,finite,displayedMeshes:meshRows,
    changedEdgeCount:edgeRatios.length,maxEdgeLengthRatio:edgeRatios.at(-1)||1,
    p95EdgeLengthRatio:edgeRatios[Math.floor(edgeRatios.length*.95)]||1,
    geometryRestUnchanged:JSON.stringify(d.geometry)===d.geometryIdentity,
    authoredInfluencesUnchanged:d.originalWeights.every(({state,indices,weights})=>
      state.indices.every((v,i)=>v===indices[i])&&state.weights.every((v,i)=>v===weights[i]))};
}"""


def evaluate_deformation(page, context, output):
    from app.runtime import server

    entries, blob = authored_skin_payload(context)
    setup = page.evaluate(
        DEFORMATION_SETUP,
        {"entries": entries, "url": server.publish_geometry(blob, replace=False)},
    )
    report = {"setup": setup, "modes": {}}
    for mode in ("current", "geometry"):
        # Select the evaluated controls only in the disposable overlay fixture.
        page.evaluate(
            """mode => {
          const preview=__evaluationRigApi.getModelRigState().geometryRigPreview;
          preview.rig=__armDeform.controls[mode];
          __evaluationRigApi.setGeometryRigPreviewIkEnabled(true);
          __evaluationRigApi.resetGeometryRigPreviewPose();
        }""",
            mode,
        )
        mode_report = {}
        for name, forward in (("forward", 0.12), ("backward", -0.12)):
            row = page.evaluate(
                """async forward => {
              const api=__evaluationRigApi, rig=api.getModelRigState().geometryRigPreview.rig;
              const target=rig.controls.leftHand.position.map((v,i)=>v+__armDeform.geometry.frame.height*(rig.frame.up[i]*.12+rig.frame.forward[i]*forward));
              api.solveGeometryRigPreviewIk('leftHand',target);
              return {positions:structuredClone(api.getModelRigState().geometryRigPreview.pose.positions),
                reached:api.getModelRigState().geometryRigPreview.pose.limbs.left_arm.reached};
            }""",
                forward,
            )
            row.update(
                page.evaluate(
                    DEFORM_POSE, {"mode": mode, "positions": row.pop("positions")}
                )
            )
            mode_report[name] = row
            for view in ("front", "side"):
                page.evaluate(
                    CAPTURE_VIEW,
                    {
                        "view": view,
                        "mode": mode,
                        "baseline": None,
                        "preservePose": True,
                        "referenceFrame": setup["referenceFrame"],
                    },
                )
                page.evaluate("""async () => {
                  const {scene,renderer,camera}=await import('./js/scene/scene.js');
                  scene.getObjectByName('viewer-inferred-rig-overlay').visible=false;
                  await renderer.renderAsync(scene,camera);
                }""")
                page.locator("#canvas-container").screenshot(
                    path=str(output / f"arm-{mode}-{name}-{view}.png")
                )
                page.evaluate("""async () => {
                  const {scene}=await import('./js/scene/scene.js');
                  scene.getObjectByName('viewer-inferred-rig-overlay').visible=true;
                }""")
        mode_report["returnToRest"] = page.evaluate(
            DEFORM_POSE, {"mode": mode, "positions": {}}
        )
        report["modes"][mode] = mode_report
    page.evaluate("""() => {
      __evaluationRigApi.getModelRigState().geometryRigPreview.rig=__armDeform.geometry;
      __evaluationRigApi.setGeometryRigPreviewIkEnabled(false);
    }""")
    return report


def main():
    from app.mods.loader import ModLoadContext, find_inis, load_mod
    from app.mods.analysis import build_mod_ini_snapshot
    from core.geometry.transport import GeometryBlob
    from core.mod_source import mod_source_for_path
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
    parser.add_argument("--near-rest", action="store_true")
    parser.add_argument("--deform-left-arm", action="store_true")
    args = parser.parse_args()
    if not (args.near_rest or args.deform_left_arm):
        parser.error("Select --near-rest or --deform-left-arm")
    args.output.mkdir(parents=True, exist_ok=True)
    mod = str(args.mod)
    source = mod_source_for_path(mod)
    context = ModLoadContext(
        mod, build_mod_ini_snapshot(find_inis(mod, source=source), mod, source=source)
    )
    blob = GeometryBlob()
    payload = load_mod(context=context, geometry=blob)
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
            "button=>button.closest('details').open=true"
        )
        page.evaluate("""async () => {
          window.__evaluationRigApi=(await import('./js/weight-rig/weight-rig-core.js')).weightRigApi;
          await __evaluationRigApi.previewGeometryRig();
          window.__armBefore={rest:JSON.stringify(__evaluationRigApi.getModelRigState().geometryRigPreview.rig),
            positions:modViewer.activeMeshes.map(m=>[...m.geometry.attributes.position.array]),
            normals:modViewer.activeMeshes.map(m=>m.geometry.attributes.normal&&[...m.geometry.attributes.normal.array])};
        }""")
        report = {"schemaVersion": 1}
        if args.near_rest:
            report["nearRest"] = evaluate_near_rest(page, args.output)
        if args.deform_left_arm:
            report["deformation"] = evaluate_deformation(page, context, args.output)
        report["invariants"] = page.evaluate("""() => ({
          restRigUnchanged:JSON.stringify(__evaluationRigApi.getModelRigState().geometryRigPreview.rig)===__armBefore.rest,
          positionsRestored:modViewer.activeMeshes.every((m,i)=>m.geometry.attributes.position.array.every((v,j)=>v===__armBefore.positions[i][j])),
          normalsRestored:modViewer.activeMeshes.every((m,i)=>!m.geometry.attributes.normal||m.geometry.attributes.normal.array.every((v,j)=>v===__armBefore.normals[i][j])),
          weightUiRequests:__bridge.calls.filter(c=>c.name==='weights').length,
          productionModelRigLoaded:__evaluationRigApi.getModelRigState().loaded})""")
        (args.output / "measurements.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "invariants": report["invariants"],
                    "deformation": report.get("deformation", {}).get("modes"),
                }
            ),
            flush=True,
        )
    finally:
        for fixture in (viewer_fixture, browser_fixture):
            try:
                next(fixture)
            except StopIteration:
                pass


if __name__ == "__main__":
    main()
