"""Isolate one-arm skeleton and ownership effects through existing helpers.

Called by evaluate_geometry_arm.py's explicit diagnostic option. Overrides
are source-scoped evaluation inputs, never production rules or authored edits.
"""

from evaluate_geometry_arm import (
    CAPTURE_VIEW,
    DEFORM_POSE,
    SOLVE_ARM_POSE,
    evaluate_deformation,
)


SELECT_SCENARIO = """async ({skeleton,ownership,overrides=[],translateShoulder=false}) => {
  const {buildHumanoidDriverFrames,restJointWorldMatrix}=await import('./js/weight-rig/humanoid-rig-binding.js');
  const {rebuildHumanoidControlPaths}=await import('./js/weight-rig/humanoid-control-rig.js');
  const d=__armDeform,rig=structuredClone(d.controls[skeleton]);
  if(translateShoulder) {
    const delta=d.controls.current.controls.leftShoulder.position.map((v,i)=>v-rig.controls.leftShoulder.position[i]);
    for(const key of ['leftShoulder','leftElbow','leftHand'])
      rig.controls[key].position=rig.controls[key].position.map((v,i)=>v+delta[i]);
    rebuildHumanoidControlPaths(rig);
  }
  const owners=new Map(d.allJointBindings[ownership]),changes=[],changedJoints=new Set();
  const drivers=buildHumanoidDriverFrames(rig);
  for(const change of overrides) {
    const jointId=d.reconciled.sourceBoneToModelJointMap.get(d.sourceBoneKey(change.sourceKey,change.boneId));
    const before=owners.get(jointId),driver=drivers.get(change.driverId);
    if(jointId===undefined||!before||!driver||changedJoints.has(jointId)||
      !['left_upper_arm','left_lower_arm'].includes(before.driverId))
      throw new Error('Invalid diagnostic arm influence override');
    changedJoints.add(jointId);
    owners.set(jointId,{...before,driverId:change.driverId,bindingMethod:'diagnostic_override',
      controlKey:driver.start,inheritedFromJointId:null});
    changes.push({...change,jointId,previousDriver:before.driverId});
  }
  const rebased=new Map();
  for(const [jointId,owner] of owners) {
    const driver=drivers.get(owner.driverId),rest=restJointWorldMatrix(d.model,jointId);
    if(!driver||!rest) throw new Error('Missing diagnostic rest frame');
    rebased.set(jointId,{...owner,localMatrix:driver.matrix.clone().invert().multiply(rest),restJointWorld:rest});
  }
  d.controls.diagnostic=rig;d.allJointBindings.diagnostic=rebased;
  d.bindings.diagnostic={jointBindings:new Map([...rebased].filter(([,owner])=>
    ['left_upper_arm','left_lower_arm'].includes(owner.driverId)))};
  const restTransforms=d.buildHumanoidDriverBaseTransforms({binding:d.bindings.diagnostic,controlRig:rig,modelRig:d.model}).result;
  const identity=new d.THREE.Matrix4().elements;
  const restTransformMaxError=Math.max(0,...[...restTransforms.values()].flatMap(matrix=>
    matrix.elements.map((v,i)=>Math.abs(v-identity[i]))));
  return {skeleton,ownership,translateShoulder,overrides:changes,
    boundArmJoints:d.bindings.diagnostic.jointBindings.size,restTransformMaxError};
}"""


EDGE_DIAGNOSTICS = """({positions,movement,freezeTracked=false,limit=8}) => {
  const d=__armDeform,rig=d.controls.diagnostic,binding=d.bindings.diagnostic;
  const {result}=d.buildHumanoidDriverBaseTransforms({binding,controlRig:rig,modelRig:d.model,posedControls:positions});
  const point=(values,v)=>new d.THREE.Vector3(...values.slice(v*3,v*3+3));
  const rows=d.commonEdges.map((edge,index)=>{
    const values=d.meshes[edge.meshIndex].geometry.attributes.position.array;
    return {...edge,index,ratio:point(values,edge.b).sub(point(values,edge.a)).length()/edge.rest};
  });
  const describe=edge=>{
    const mesh=d.meshes[edge.meshIndex],state=d.runtime.stateFor(mesh),base=d.baselines[edge.meshIndex].positions;
    const restPoints=[edge.a,edge.b].map(v=>point(base,v));
    const posedPoints=[edge.a,edge.b].map(v=>point(mesh.geometry.attributes.position.array,v));
    const posedVector=posedPoints[1].clone().sub(posedPoints[0]);
    const contributions=new Map(),reconstructed=restPoints[1].clone().sub(restPoints[0]);
    for(const [endpoint,vertex] of [edge.a,edge.b].entries()) {
      let transformedWeight=0;
      for(let slot=0;state.loaded&&slot<state.influenceCount;slot++) {
        const at=vertex*state.influenceCount+slot,boneId=state.indices[at],weight=state.weights[at];
        if(!(weight>0)) continue;
        const jointId=d.reconciled.sourceBoneToModelJointMap.get(d.sourceBoneKey(state.skinningSourceKey,boneId));
        const matrix=result.get(jointId),owner=d.allJointBindings.diagnostic.get(jointId);
        let row=contributions.get(boneId);
        if(!row) {
          const joint=d.model.joints.find(j=>j.jointId===jointId);
          const pivot=d.model.jointPivotByJointId.get(jointId);
          row={sourceKey:state.skinningSourceKey,boneId,jointId,driverId:owner?.driverId||null,
            bindingMethod:owner?.bindingMethod||null,inheritedFromJointId:owner?.inheritedFromJointId??null,
            jointMembers:joint?.members||[],restJointPivot:pivot?.isVector3?pivot.toArray():
              Array.isArray(pivot)?pivot:pivot?[pivot.x,pivot.y,pivot.z]:null,
            endpointWeights:[0,0],vector:new d.THREE.Vector3()};contributions.set(boneId,row);
        }
        row.endpointWeights[endpoint]+=weight;
        if(matrix) {
          transformedWeight+=weight;
          row.vector.addScaledVector(restPoints[endpoint].clone().applyMatrix4(matrix).sub(restPoints[endpoint]),
            weight*(endpoint?1:-1));
        }
      }
      // The existing helper retains max(0,1-sum) of each original point.
      // Account for over-unit authored sums without normalizing any weights.
      reconstructed.addScaledVector(restPoints[endpoint],Math.max(0,transformedWeight-1)*(endpoint?1:-1));
    }
    const influences=[...contributions.values()].map(row=>{
      reconstructed.add(row.vector);
      return {...row,vector:row.vector.toArray(),contributionRestLength:row.vector.length()/edge.rest,
        projectedContributionRestLength:row.vector.dot(posedVector.clone().normalize())/edge.rest,
        withoutInfluenceRatio:posedVector.clone().sub(row.vector).length()/edge.rest};
    }).sort((a,b)=>b.contributionRestLength-a.contributionRestLength);
    return {sampleIndex:edge.index,mesh:mesh.userData.semanticKey,vertices:[edge.a,edge.b],
      restPoints:restPoints.map(p=>p.toArray()),restLengthHeight:edge.rest/d.geometry.frame.height,
      ratio:edge.ratio,reconstructionErrorHeight:reconstructed.distanceTo(posedVector)/d.geometry.frame.height,influences};
  };
  rows.sort((a,b)=>b.ratio-a.ratio);
  if(freezeTracked) {
    d.shoulderTracked??={};
    d.shoulderTracked[movement]=[...rows.slice(0,limit),...rows.slice(-limit)].map(e=>e.index);
  }
  const tracked=d.shoulderTracked?.[movement]||[],byIndex=new Map(rows.map(e=>[e.index,e.ratio]));
  return {stretched:rows.slice(0,limit).map(describe),compressed:rows.slice(-limit).reverse().map(describe),
    trackedEdges:{sampleIndices:tracked,ratios:tracked.map(index=>byIndex.get(index))},
    distribution:{edgeCount:rows.length,aboveDouble:rows.filter(e=>e.ratio>2).length,
      belowHalf:rows.filter(e=>e.ratio<.5).length,
      meanAbsoluteLogRatio:rows.reduce((sum,e)=>sum+Math.abs(Math.log(e.ratio)),0)/rows.length}};
}"""


INFLUENCE_SUPPORT = """jointIds => {
  const d=__armDeform,wanted=new Set(jointIds),rows=new Map();
  const shoulder=new d.THREE.Vector3(...d.controls.geometry.controls.leftShoulder.position);
  const elbow=new d.THREE.Vector3(...d.controls.geometry.controls.leftElbow.position);
  const upper=elbow.clone().sub(shoulder),height=d.geometry.frame.height;
  for(let meshIndex=0;meshIndex<d.meshes.length;meshIndex++) {
    const mesh=d.meshes[meshIndex],state=d.runtime.stateFor(mesh);
    if(!mesh.visible||!state.loaded) continue;
    const indices=mesh.geometry.index?.array,count=indices?.length||mesh.geometry.attributes.position.count;
    const start=mesh.geometry.drawRange.start,end=Math.min(count,start+mesh.geometry.drawRange.count);
    const vertices=new Set(Array.from({length:end-start},(_,i)=>indices?indices[start+i]:start+i));
    for(const vertex of vertices) {
      const point=new d.THREE.Vector3(...d.baselines[meshIndex].positions.slice(vertex*3,vertex*3+3));
      const parameter=point.clone().sub(shoulder).dot(upper)/upper.lengthSq();
      const weights=new Map();
      for(let slot=0;slot<state.influenceCount;slot++) {
        const at=vertex*state.influenceCount+slot,boneId=state.indices[at],weight=state.weights[at];
        if(weight>0) weights.set(boneId,(weights.get(boneId)||0)+weight);
      }
      for(const [boneId,weight] of weights) {
        const jointId=d.reconciled.sourceBoneToModelJointMap.get(d.sourceBoneKey(state.skinningSourceKey,boneId));
        if(!wanted.has(jointId)) continue;
        const key=d.sourceBoneKey(state.skinningSourceKey,boneId);
        let row=rows.get(key);
        if(!row) {
          row={sourceKey:state.skinningSourceKey,boneId,jointId,displayedVertexCount:0,totalWeight:0,
            weightedRestCenter:new d.THREE.Vector3(),bounds:new d.THREE.Box3(),upperArmParameter:0,
            proximalWeight:0,distalWeight:0};rows.set(key,row);
        }
        row.displayedVertexCount++;row.totalWeight+=weight;row.weightedRestCenter.addScaledVector(point,weight);
        row.bounds.expandByPoint(point);row.upperArmParameter+=weight*parameter;
        if(parameter<=1) row.proximalWeight+=weight;else row.distalWeight+=weight;
      }
    }
  }
  return [...rows.values()].map(row=>({...row,
    weightedRestCenter:row.weightedRestCenter.divideScalar(row.totalWeight).toArray(),
    bounds:{min:row.bounds.min.toArray(),max:row.bounds.max.toArray()},
    upperArmParameter:row.upperArmParameter/row.totalWeight,
    weightProximalToElbow:row.proximalWeight/row.totalWeight,
    weightDistalToElbow:row.distalWeight/row.totalWeight,height}));
}"""


SHOULDER_CAPTURE = """async () => {
  const {scene,renderer,camera}=await import('./js/scene/scene.js');
  const d=__armDeform,mesh=d.meshes.find(m=>m.visible);
  mesh.updateWorldMatrix(true,false);
  const canvas=renderer.domElement.getBoundingClientRect();
  const frame=d.geometry.frame,shoulder=d.geometry.controls.leftShoulder.position;
  const center=new d.THREE.Vector3(...shoulder);
  const points=[];
  // One frozen anatomical crop covers the shoulder, nearby cloth and elbow.
  for(const side of [-.19,.13]) for(const up of [-.18,.11]) for(const depth of [-.14,.14])
    points.push(center.clone().addScaledVector(new d.THREE.Vector3(...frame.right),side*frame.height)
      .addScaledVector(new d.THREE.Vector3(...frame.up),up*frame.height)
      .addScaledVector(new d.THREE.Vector3(...frame.forward),depth*frame.height)
      .applyMatrix4(mesh.matrixWorld).project(camera));
  const x=points.map(p=>(p.x+1)*canvas.width/2),y=points.map(p=>(1-p.y)*canvas.height/2);
  const overlay=scene.getObjectByName('viewer-inferred-rig-overlay');
  if(overlay) overlay.visible=false;
  await renderer.renderAsync(scene,camera);
  const left=Math.max(0,Math.floor(Math.min(...x))),top=Math.max(0,Math.floor(Math.min(...y)));
  return {x:canvas.x+left,y:canvas.y+top,
    width:Math.min(canvas.width,Math.ceil(Math.max(...x)))-left,
    height:Math.min(canvas.height,Math.ceil(Math.max(...y)))-top};
}"""


def evaluate_shoulder(
    page,
    context,
    output,
    *,
    overrides=(),
    capture_views=("front", "side"),
    baseline=None,
):
    """Cross skeletons with frozen owners, then test explicit diagnostic inputs."""
    baseline = baseline or evaluate_deformation(page, context, output, capture_views=())
    scenarios = [("geometry_geometry_owners", "geometry", "geometry", [], False)] + [
        (f"{skeleton}_{ownership}_owners", skeleton, ownership, [], False)
        for skeleton in ("current", "geometry")
        for ownership in ("current", "geometry")
        if (skeleton, ownership) != ("geometry", "geometry")
    ]
    scenarios.append(("geometry_shifted_pivot", "geometry", "geometry", [], True))
    if overrides:
        groups = sorted({change.get("group", "diagnostic") for change in overrides})
        for group in groups:
            changes = [
                change
                for change in overrides
                if change.get("group", "diagnostic") == group
            ]
            scenarios.append(
                (f"geometry_{group}", "geometry", "geometry", changes, False)
            )
        scenarios.append(
            ("geometry_corrected_owners", "geometry", "geometry", overrides, False)
        )
    report = {"setup": baseline["setup"], "scenarios": {}}
    try:
        for name, skeleton, ownership, changes, translated in scenarios:
            selection = page.evaluate(
                SELECT_SCENARIO,
                {
                    "skeleton": skeleton,
                    "ownership": ownership,
                    "overrides": changes,
                    "translateShoulder": translated,
                },
            )
            rows = {"selection": selection}
            for movement, forward in (("forward", 0.12), ("backward", -0.12)):
                solved = page.evaluate(
                    SOLVE_ARM_POSE, {"mode": "diagnostic", "forward": forward}
                )
                positions = solved.pop("positions")
                deformed = page.evaluate(
                    DEFORM_POSE, {"mode": "diagnostic", "positions": positions}
                )
                deformed.pop("displayedMeshes")
                edges = page.evaluate(
                    EDGE_DIAGNOSTICS,
                    {
                        "positions": positions,
                        "movement": movement,
                        "freezeTracked": name == "geometry_geometry_owners",
                    },
                )
                for kind in ("stretched", "compressed"):
                    group = edges[kind]
                    for edge in group:
                        manifest = context.skinning_manifest.get(edge["mesh"])
                        if manifest:
                            edge["authoredVertices"] = [
                                manifest.used_vertices[v] for v in edge["vertices"]
                            ]
                rows[movement] = {**solved, **deformed, "worstEdges": edges}
                if overrides and name in (
                    "geometry_geometry_owners",
                    "geometry_corrected_owners",
                ):
                    for view in capture_views:
                        page.evaluate(
                            CAPTURE_VIEW,
                            {
                                "view": view,
                                "mode": "geometry",
                                "baseline": None,
                                "preservePose": True,
                                "referenceFrame": baseline["setup"]["referenceFrame"],
                            },
                        )
                        clip = page.evaluate(SHOULDER_CAPTURE)
                        page.screenshot(
                            path=str(output / f"{name}-{movement}-{view}.png"),
                            clip=clip,
                        )
            rows["returnToRest"] = page.evaluate(
                DEFORM_POSE, {"mode": "diagnostic", "positions": {}}
            )
            report["scenarios"][name] = rows
    finally:
        page.evaluate(DEFORM_POSE, {"mode": "geometry", "positions": {}})
        page.evaluate("""() => {
          delete __armDeform.controls.diagnostic;delete __armDeform.bindings.diagnostic;
          delete __armDeform.allJointBindings.diagnostic;
          delete __armDeform.shoulderTracked;
        }""")
    selected = report["scenarios"]["geometry_geometry_owners"]
    joint_ids = sorted(
        {
            influence["jointId"]
            for direction in ("forward", "backward")
            for kind in ("stretched", "compressed")
            for edges in [selected[direction]["worstEdges"][kind]]
            for edge in edges
            for influence in edge["influences"]
            if influence["jointId"] is not None
        }
    )
    joint_ids = sorted(
        set(joint_ids)
        | {
            change["jointId"]
            for change in report["scenarios"]
            .get("geometry_corrected_owners", {})
            .get("selection", {})
            .get("overrides", [])
        }
    )
    report["influenceSupport"] = page.evaluate(INFLUENCE_SUPPORT, joint_ids)
    return report
