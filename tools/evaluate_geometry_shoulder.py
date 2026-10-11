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


SELECT_SCENARIO = """async ({skeleton,ownership,overrides=[],translateShoulder=false,includeUpperBody=false}) => {
  const {buildHumanoidDriverFrames,restJointWorldMatrix}=await import('./js/weight-rig/humanoid-rig-binding.js');
  const {rebuildHumanoidControlPaths}=await import('./js/weight-rig/humanoid-control-rig.js');
  const d=__armDeform,rig=structuredClone(d.controls[skeleton]),side=d.side||'left';
  if(translateShoulder) {
    const delta=d.controls.current.controls[side+'Shoulder'].position.map((v,i)=>v-rig.controls[side+'Shoulder'].position[i]);
    for(const key of ['Shoulder','Elbow','Hand'].map(key=>side+key))
      rig.controls[key].position=rig.controls[key].position.map((v,i)=>v+delta[i]);
    rebuildHumanoidControlPaths(rig);
  }
  const owners=new Map(d.allJointBindings[ownership]),changes=[],changedJoints=new Set();
  const drivers=buildHumanoidDriverFrames(rig);
  for(const change of overrides) {
    const jointId=d.reconciled.sourceBoneToModelJointMap.get(d.sourceBoneKey(change.sourceKey,change.boneId));
    const before=owners.get(jointId),driver=drivers.get(change.driverId);
    if(jointId===undefined||!before||!driver||changedJoints.has(jointId)||
      !['left_upper_arm','left_lower_arm','right_upper_arm','right_lower_arm'].includes(before.driverId))
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
  d.bindings.diagnostic={jointBindings:new Map([...rebased].filter(([,owner])=>includeUpperBody
    ? !owner.driverId.includes('leg')&&!owner.driverId.includes('hip')
    : [`${side}_upper_arm`,`${side}_lower_arm`].includes(owner.driverId)))};
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
    const active=influences.filter(row=>result.has(row.jointId));
    let singleDriverBlend=null;
    if(active.length&&new Set(active.map(row=>row.driverId)).size===1) {
      const matrix=result.get(active[0].jointId),midpoint=restPoints[0].clone().add(restPoints[1]).multiplyScalar(.5);
      const weights=[0,1].map(endpoint=>active.reduce((sum,row)=>sum+row.endpointWeights[endpoint],0));
      const restEdge=restPoints[1].clone().sub(restPoints[0]);
      const rotated=restEdge.clone().applyMatrix3(new d.THREE.Matrix3().setFromMatrix4(matrix));
      const uniform=restEdge.clone().addScaledVector(rotated.clone().sub(restEdge),(weights[0]+weights[1])*.5);
      const gradient=midpoint.clone().applyMatrix4(matrix).sub(midpoint).multiplyScalar(weights[1]-weights[0]);
      const overUnit=restPoints[1].clone().multiplyScalar(Math.max(0,weights[1]-1))
        .addScaledVector(restPoints[0],-Math.max(0,weights[0]-1));
      singleDriverBlend={driverId:active[0].driverId,endpointMovingWeights:weights,
        rigidRatio:rotated.length()/edge.rest,uniformWeightRatio:uniform.length()/edge.rest,
        uniformVector:uniform.toArray(),weightGradientVector:gradient.toArray(),
        overUnitVector:overUnit.toArray(),
        gradientRestLength:gradient.length()/edge.rest,
        reconstructedRatio:uniform.clone().add(gradient).add(overUnit).length()/edge.rest};
    }
    return {sampleIndex:edge.index,mesh:mesh.userData.semanticKey,vertices:[edge.a,edge.b],
      restPoints:restPoints.map(p=>p.toArray()),restLengthHeight:edge.rest/d.geometry.frame.height,
      ratio:edge.ratio,reconstructionErrorHeight:reconstructed.distanceTo(posedVector)/d.geometry.frame.height,influences,singleDriverBlend};
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
  const frame=d.geometry.frame,side=d.side||'left',shoulder=d.geometry.controls[side+'Shoulder'].position;
  const center=new d.THREE.Vector3(...shoulder);
  const points=[];
  // One frozen anatomical crop covers the shoulder, nearby cloth and elbow.
  for(const lateral of side==='left'?[-.19,.13]:[-.13,.19]) for(const up of [-.18,.11]) for(const depth of [-.14,.14])
    points.push(center.clone().addScaledVector(new d.THREE.Vector3(...frame.right),lateral*frame.height)
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


SUPPORT_OWNERSHIP = """async () => {
  const {buildHumanoidDriverFrames}=await import('./js/weight-rig/humanoid-rig-binding.js');
  const d=__armDeform,rig=d.controls.geometry,drivers=[...buildHumanoidDriverFrames(rig).values()];
  const armIds=new Set(['left_upper_arm','left_lower_arm','right_upper_arm','right_lower_arm']);
  const candidates=new Map([...d.allJointBindings.geometry].filter(([,owner])=>armIds.has(owner.driverId))
    .map(([jointId,owner])=>[jointId,{jointId,owner,mass:0,scores:new Array(drivers.length).fill(0)}]));
  const distance=(point,driver)=>{
    const delta=driver.end.clone().sub(driver.start),at=d.THREE.MathUtils.clamp(
      point.clone().sub(driver.start).dot(delta)/delta.lengthSq(),0,1);
    return point.distanceToSquared(driver.start.clone().addScaledVector(delta,at));
  };
  let displayedTriangles=0;
  for(let meshIndex=0;meshIndex<d.meshes.length;meshIndex++) {
    const mesh=d.meshes[meshIndex],state=d.runtime.stateFor(mesh);
    if(!mesh.visible||!state.loaded) continue;
    const base=d.baselines[meshIndex].positions,indices=mesh.geometry.index?.array;
    const count=indices?.length||base.length/3,start=mesh.geometry.drawRange.start;
    const end=Math.min(count,start+mesh.geometry.drawRange.count),areas=new Float64Array(base.length/3);
    const point=v=>new d.THREE.Vector3(...base.slice(v*3,v*3+3));
    for(let at=start;at+2<end;at+=3) {
      const vertices=[0,1,2].map(i=>indices?indices[at+i]:at+i),points=vertices.map(point);
      const area=points[1].clone().sub(points[0]).cross(points[2].clone().sub(points[0])).length()/2;
      if(!(area>0)) continue;displayedTriangles++;
      for(const vertex of vertices) areas[vertex]+=area/3;
    }
    for(let vertex=0;vertex<areas.length;vertex++) {
      if(!(areas[vertex]>0)) continue;
      const distances=drivers.map(driver=>distance(point(vertex),driver));
      for(let slot=0;slot<state.influenceCount;slot++) {
        const at=vertex*state.influenceCount+slot,weight=state.weights[at];
        const jointId=d.reconciled.sourceBoneToModelJointMap.get(d.sourceBoneKey(state.skinningSourceKey,state.indices[at]));
        const row=candidates.get(jointId);
        if(!row||!(weight>0)) continue;
        const mass=areas[vertex]*weight;row.mass+=mass;
        distances.forEach((value,i)=>row.scores[i]+=mass*value);
      }
    }
  }
  const facts=[],support=[],pivot=[],withinArm=[];
  for(const row of candidates.values()) {
    if(!(row.mass>0)) continue;
    const joint=d.model.joints.find(j=>j.jointId===row.jointId),member=joint.members[0];
    const raw=d.model.jointPivotByJointId.get(row.jointId);
    const point=raw?.isVector3?raw.clone():new d.THREE.Vector3(...(Array.isArray(raw)?raw:[raw.x,raw.y,raw.z]));
    const scores=drivers.map((driver,i)=>({driverId:driver.id,
      supportScoreHeightSquared:row.scores[i]/row.mass/rig.frame.height**2,
      pivotScoreHeightSquared:distance(point,driver)/rig.frame.height**2}));
    const bySupport=[...scores].sort((a,b)=>a.supportScoreHeightSquared-b.supportScoreHeightSquared||a.driverId.localeCompare(b.driverId));
    const byPivot=[...scores].sort((a,b)=>a.pivotScoreHeightSquared-b.pivotScoreHeightSquared||a.driverId.localeCompare(b.driverId));
    const side=row.owner.driverId.startsWith('left_')?'left':'right';
    const armScores=bySupport.filter(score=>[`${side}_upper_arm`,`${side}_lower_arm`].includes(score.driverId));
    const input={sourceKey:member.sourceKey,boneId:member.boneId,group:'spatial_support'};
    if(bySupport[0].driverId!==row.owner.driverId) support.push({...input,driverId:bySupport[0].driverId});
    if(byPivot[0].driverId!==row.owner.driverId) pivot.push({...input,driverId:byPivot[0].driverId});
    if(armScores[0].driverId!==row.owner.driverId) withinArm.push({...input,driverId:armScores[0].driverId});
    facts.push({jointId:row.jointId,jointMembers:joint.members,originalDriver:row.owner.driverId,
      originalBindingMethod:row.owner.bindingMethod,restJointPivot:point.toArray(),supportMass:row.mass,
      supportChoice:bySupport[0].driverId,pivotChoice:byPivot[0].driverId,
      originalScores:scores.find(score=>score.driverId===row.owner.driverId),
      armSegmentChoice:armScores[0].driverId,armSegmentScores:armScores,
      supportRanking:bySupport.slice(0,3),pivotRanking:byPivot.slice(0,3)});
  }
  return {definition:'area_lumped_authored_weight_mean_squared_distance_to_existing_rest_driver_segments',
    scope:'existing_upper_or_lower_arm_owners_only',displayedTriangles,
    facts,supportOverrides:support,pivotOverrides:pivot,armSegmentOverrides:withinArm};
}"""


TORSO_POSE = """() => {
  const d=__armDeform,rig=d.controls.diagnostic,pivot=new d.THREE.Vector3(...rig.controls.pelvis.position);
  const rotation=new d.THREE.Quaternion().setFromAxisAngle(new d.THREE.Vector3(...rig.frame.right),6*Math.PI/180);
  const keys=['chest','neck','head',...['left','right'].flatMap(side=>['Shoulder','Elbow','Hand'].map(key=>side+key))];
  const positions=Object.fromEntries(keys.map(key=>[key,new d.THREE.Vector3(...rig.controls[key].position)
    .sub(pivot).applyQuaternion(rotation).add(pivot).toArray()]));
  return {positions,pose:{rotationDegrees:6,axis:rig.frame.right,pivot:pivot.toArray(),
    restControls:Object.fromEntries(keys.map(key=>[key,rig.controls[key].position])),solvedControls:positions}};
}"""


def evaluate_binding_study(
    page, context, output, *, overrides=(), capture_views=("front",)
):
    """Evaluate one fixed support metric on both arms and a coupled torso pose."""
    report = {}
    for side in ("left", "right"):
        baseline = evaluate_deformation(
            page, context, output, capture_views=(), side=side
        )
        evidence = page.evaluate(SUPPORT_OWNERSHIP)
        candidates = [
            ("original", []),
            ("pivot_segments", evidence["pivotOverrides"]),
            ("support_segments", evidence["supportOverrides"]),
            ("support_within_arm", evidence["armSegmentOverrides"]),
        ]
        if overrides:
            candidates.append(("diagnostic_six", overrides))
        study = {"setup": baseline["setup"], "evidence": evidence, "cases": {}}
        try:
            for name, changes in candidates:
                cases = {}
                for movement, forward, scale in (
                    ("forward_half", 0.12, 0.5),
                    ("forward_full", 0.12, 1),
                    ("backward_half", -0.12, 0.5),
                    ("backward_full", -0.12, 1),
                    ("torso_small", 0, 0),
                ):
                    selection = page.evaluate(
                        SELECT_SCENARIO,
                        {
                            "skeleton": "geometry",
                            "ownership": "geometry",
                            "overrides": changes,
                            "includeUpperBody": movement == "torso_small",
                        },
                    )
                    solved = (
                        page.evaluate(TORSO_POSE)
                        if movement == "torso_small"
                        else page.evaluate(
                            SOLVE_ARM_POSE,
                            {"mode": "diagnostic", "forward": forward, "scale": scale},
                        )
                    )
                    positions = solved.pop("positions")
                    row = page.evaluate(
                        DEFORM_POSE, {"mode": "diagnostic", "positions": positions}
                    )
                    if movement == "torso_small":
                        row["diagnosticInfluences"] = [
                            {
                                "mesh": mesh["mesh"],
                                **{
                                    key: trace[key]
                                    for key in (
                                        "sourceKey",
                                        "boneId",
                                        "jointId",
                                        "driverId",
                                        "vertexCount",
                                    )
                                },
                            }
                            for mesh in row["displayedMeshes"]
                            for trace in mesh["movingInfluences"]
                            if any(
                                trace["sourceKey"] == change["sourceKey"]
                                and trace["boneId"] == change["boneId"]
                                for change in overrides
                            )
                        ]
                    # Retain draw movement totals, without repeating full influence traces.
                    row["displayedMeshes"] = [
                        {"mesh": mesh["mesh"], "movedVertices": mesh["movedVertices"]}
                        for mesh in row["displayedMeshes"]
                    ]
                    edges = page.evaluate(
                        EDGE_DIAGNOSTICS,
                        {
                            "positions": positions,
                            "movement": movement,
                            "freezeTracked": name == "original",
                            "limit": 2,
                        },
                    )
                    for kind in ("stretched", "compressed"):
                        for edge in edges[kind]:
                            manifest = context.skinning_manifest.get(edge["mesh"])
                            if manifest:
                                edge["authoredVertices"] = [
                                    manifest.used_vertices[v] for v in edge["vertices"]
                                ]
                    cases[movement] = {
                        **solved,
                        **row,
                        "selection": selection,
                        "worstEdges": edges,
                    }
                    capture = (
                        side == "right"
                        and name in ("original", "support_within_arm")
                        and movement == "backward_half"
                    ) or (
                        side == "left"
                        and name in ("original", "diagnostic_six")
                        and movement == "torso_small"
                    )
                    if capture:
                        for view in capture_views:
                            page.evaluate(
                                CAPTURE_VIEW,
                                {
                                    "view": view,
                                    "mode": "geometry",
                                    "baseline": None,
                                    "preservePose": True,
                                    "referenceFrame": baseline["setup"][
                                        "referenceFrame"
                                    ],
                                },
                            )
                            clip = page.evaluate(SHOULDER_CAPTURE)
                            page.screenshot(
                                path=str(
                                    output
                                    / f"study-{side}-{name}-{movement}-{view}.png"
                                ),
                                clip=clip,
                            )
                cases["returnToRest"] = page.evaluate(
                    DEFORM_POSE, {"mode": "diagnostic", "positions": {}}
                )
                study["cases"][name] = cases
        finally:
            page.evaluate(DEFORM_POSE, {"mode": "geometry", "positions": {}})
            page.evaluate("""() => {
              delete __armDeform.controls.diagnostic;delete __armDeform.bindings.diagnostic;
              delete __armDeform.allJointBindings.diagnostic;delete __armDeform.shoulderTracked;
            }""")
        report[side] = study
    return report
