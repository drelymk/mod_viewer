from .support import *
from PIL import ImageChops

def _set_ao_level(page, level):
    before = page.evaluate("""async () => {
      const {getViewportRenderPipelineDebugState} =
        await import('./js/scene/scene.js');
      const state = getViewportRenderPipelineDebugState();
      return {level: Math.round(state.strength * 100),
              renderCount: window.modViewer.getRenderCount()};
    }""")
    if page.locator("#ao-popover").is_hidden():
        page.locator("#ao-btn").click()
    page.evaluate("""value => {
      const slider = document.querySelector('#ao-slider');
      slider.value = String(value);
      slider.dispatchEvent(new Event('input', {bubbles: true}));
    }""", level)
    page.wait_for_function("""state => {
      const slider = document.querySelector('#ao-slider');
      const expectedStrength = state.level / 100;
      const current = window.modViewer.getAmbientOcclusionStrength();
      return Number(slider.value) === state.level
        && Math.abs(current - expectedStrength) < 1e-9
        && (state.level === state.previousLevel
          || window.modViewer.getRenderCount() > state.renderCount);
    }""", arg={
        "level": level,
        "previousLevel": before["level"],
        "renderCount": before["renderCount"],
    })


def _set_test_key_light(page, x=1.0, z=0.25):
    page.evaluate("""async ({x, z}) => {
      const THREE = await import('three');
      const {scene, controls} = await import('./js/scene/scene.js');
      const {requestRender} = await import('./js/scene/render-scheduler.js');
      let key = null;
      scene.traverse(object => {
        if (object.isAmbientLight || object.isHemisphereLight) {
          object.intensity = 0;
        } else if (object.isSprite || object.isGridHelper) {
          object.visible = false;
        } else if (object.isDirectionalLight) {
          if (!key) key = object;
          else object.intensity = 0;
        }
      });
      key.target.position.copy(controls.target);
      key.position.copy(controls.target).add(new THREE.Vector3(x, 0, z));
      key.intensity = 1;
      requestRender();
    }""", {"x": x, "z": z})
    page.wait_for_timeout(400)


def _outline_payload():
    payload = _payload("Outline")
    positions = (
        0, 1, 0,
        1, 0, 0,
        0, 0, 1,
        -1, 0, 0,
        0, 0, -1,
        0, -1, 0,
    )
    indices = (
        0, 2, 1, 0, 3, 2, 0, 4, 3, 0, 1, 4,
        5, 1, 2, 5, 2, 3, 5, 3, 4, 5, 4, 1,
    )
    template = next(iter(payload["meshes"].values()))
    template.update({
        "component": "Outline Near",
        "drawindexed": [len(indices), 0, 0],
        "pos": _f32(*positions),
        "normal": _f32(*positions),
        "idx": _u32(*indices),
        "tex_key": None,
        "texture_pool_id": None,
        "texture_variants": [],
        "shape_targets": [],
    })
    second = copy.deepcopy(template)
    second["component"] = "Outline Far"
    payload["meshes"] = {
        "Outline-Near-0": template,
        "Outline-Far-0": second,
    }
    payload["texture_pools"] = {}
    payload["textures"] = {}
    return payload


def _outline_widths(page):
    canvas = page.locator("#canvas-container canvas")

    def set_enabled(value):
        before = page.evaluate("window.modViewer.getRenderCount()")
        page.evaluate("""async value => {
          const {setOutlinesEnabled} =
            await import('./js/scene/outline-renderer.js');
          setOutlinesEnabled(value);
        }""", value)
        page.wait_for_function("""({value, before}) =>
          window.modViewer.getOutlineState(0).visible === value
          && window.modViewer.getRenderCount() > before
        """, arg={"value": value, "before": before})

    set_enabled(False)
    bounds = page.evaluate("""async () => {
      const THREE = await import('three');
      const {camera, renderer} = await import('./js/scene/scene.js');
      camera.updateMatrixWorld();
      const rect = renderer.domElement.getBoundingClientRect();
      return window.modViewer.activeMeshes
        .filter(mesh => mesh.visible)
        .map(mesh => {
          mesh.updateWorldMatrix(true, true);
          const position = mesh.geometry.attributes.position;
          const points = [];
          for (let i = 0; i < position.count; i += 1) {
            const projected = new THREE.Vector3()
              .fromBufferAttribute(position, i)
              .applyMatrix4(mesh.matrixWorld)
              .project(camera);
            points.push({
              x: (projected.x + 1) * rect.width / 2,
              y: (1 - projected.y) * rect.height / 2,
            });
          }
          const center = new THREE.Vector3()
            .applyMatrix4(mesh.matrixWorld)
            .project(camera);
          return {
            minX: Math.min(...points.map(point => point.x)),
            maxX: Math.max(...points.map(point => point.x)),
            centerY: (1 - center.y) * rect.height / 2,
          };
        });
    }""")
    without_outline = Image.open(
        io.BytesIO(canvas.screenshot())).convert("RGB")
    set_enabled(True)
    with_outline = Image.open(
        io.BytesIO(canvas.screenshot())).convert("RGB")
    difference = ImageChops.difference(without_outline, with_outline)

    widths = []
    for item in bounds:
        row_widths = []
        center_y = round(item["centerY"])
        for y in range(center_y - 2, center_y + 3):
            if not 0 <= y < difference.height:
                continue
            windows = (
                (math.floor(item["minX"]) - 6,
                 math.ceil(item["minX"]) + 2),
                (math.floor(item["maxX"]) - 2,
                 math.ceil(item["maxX"]) + 6),
            )
            for start, end in windows:
                changed = [
                    x for x in range(max(0, start), min(difference.width, end + 1))
                    if max(difference.getpixel((x, y))) > 0
                ]
                if changed:
                    row_widths.append(len(changed))
        assert row_widths, (item, difference.getbbox())
        ordered = sorted(row_widths)
        widths.append(ordered[len(ordered) // 2])
    return widths


def test_webgpu_startup_uses_actual_webgpu_backend(edge_browser, frontend_url):
    context = edge_browser.new_context(bypass_csp=True)
    page = context.new_page()
    try:
        page.goto(frontend_url)
        page.locator("#open-btn:not([disabled])").wait_for(timeout=10000)
        page.wait_for_function(
            "import('./js/scene/scene.js').then(({renderer}) => renderer.currentSamples === 4)")
        state = page.evaluate("""async () => {
          const {renderer, scene} = await import('./js/scene/scene.js');
          const keyLight = scene.children.find(object => object.isDirectionalLight);
          return {
            isWebGPURenderer: renderer.isWebGPURenderer === true,
            isWebGPUBackend: renderer.backend?.isWebGPUBackend === true,
            compatibilityMode: renderer.backend?.compatibilityMode,
            samples: renderer.samples,
            animationLoop: renderer.getAnimationLoop() !== null,
            outputColorSpace: renderer.outputColorSpace,
            toneMapping: renderer.toneMapping,
            clearAlpha: renderer.getClearAlpha(),
            shadowsEnabled: renderer.shadowMap.enabled,
            keyCastsShadow: keyLight?.castShadow,
            shadowAutoUpdate: keyLight?.shadow?.autoUpdate,
            shadowMapSize: [keyLight?.shadow?.mapSize?.x, keyLight?.shadow?.mapSize?.y],
          };
        }""")
        assert state["isWebGPURenderer"]
        assert state["isWebGPUBackend"]
        assert state["compatibilityMode"] is False
        assert state["samples"] == 4
        assert not state["animationLoop"]
        assert state["outputColorSpace"] == "srgb"
        assert state["toneMapping"] == 0
        assert state["clearAlpha"] == 1
        assert state["shadowsEnabled"]
        assert state["keyCastsShadow"]
        assert state["shadowAutoUpdate"] is False
        assert state["shadowMapSize"] == [2048, 2048]
    finally:
        context.close()

def test_skinning_physics_solver_covers_targets_kicks_and_equilibrium(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"Physics": _payload("Physics")})
    try:
        result = page.evaluate("""async () => {
          const physics = await import('./js/mesh/weight-physics.js');
          const deformation = await import('./js/mesh/weight-deformation.js');
          const forest = {
            primaryRootId: 0,
            components: [{
              componentId: 0, rootId: 0, primary: true,
              nodeIds: [0, 1, 2, 3], maxDepth: 2,
              depthById: {0: 0, 1: 1, 2: 2, 3: 2},
              childrenById: {0: [1], 1: [2, 3]},
            }],
          };
          const dt = 1 / 120;
          const target = Math.PI * 40 / 180;
          const targetAngles = physics.buildPhysicsTargetAngles(forest, target);
          const zeroState = physics.initializePhysicsState(forest);
          physics.stepSpringPhysics(zeroState, forest, dt, {
            targetAngleRadians: 0, frequencyHz: 2, dampingRatio: .35,
          });

          const under = physics.initializePhysicsState(forest);
          let peak = 0;
          for (let index = 0; index < 360; index += 1) {
            physics.stepSpringPhysics(under, forest, dt, {
              targetAngleRadians: target, frequencyHz: 2, dampingRatio: .35,
            });
            peak = Math.max(peak, under.joints.get(2).angle);
          }
          const underFinal = under.joints.get(2).angle;
          for (let index = 0; index < 1200; index += 1) {
            physics.stepSpringPhysics(under, forest, dt, {
              targetAngleRadians: target, frequencyHz: 2, dampingRatio: .35,
            });
          }
          const settled = physics.isPhysicsSettled(under, forest, target, {
            angleTolerance: .002, velocityTolerance: .002,
          });

          const critical = physics.initializePhysicsState(forest);
          let criticalPeak = 0;
          for (let index = 0; index < 360; index += 1) {
            physics.stepSpringPhysics(critical, forest, dt, {
              targetAngleRadians: target, frequencyHz: 2, dampingRatio: 1,
            });
            criticalPeak = Math.max(criticalPeak, critical.joints.get(2).angle);
          }

          const kicked = physics.initializePhysicsState(forest);
          physics.applyPhysicsKick(kicked, forest, 2);
          const kick = [...kicked.joints.entries()].map(([boneId, joint]) =>
            [boneId, joint.angularVelocity]);
          const reset = physics.resetPhysicsState(kicked);
          const fixedA = physics.initializePhysicsState(forest);
          const fixedB = physics.initializePhysicsState(forest);
          for (let index = 0; index < 240; index += 1) {
            physics.stepSpringPhysics(fixedA, forest, dt, {
              targetAngleRadians: target, frequencyHz: 2, dampingRatio: .35,
            });
          }
          for (let index = 0; index < 240; index += 1) {
            physics.stepSpringPhysics(fixedB, forest, dt, {
              targetAngleRadians: target, frequencyHz: 2, dampingRatio: .35,
            });
          }

          const centers = new Map([
            [0, [0, 0, 0]], [1, [1, 0, 0]],
            [2, [2, 0, 0]], [3, [1, 1, 0]],
          ]);
          const dynamicTransforms = deformation.buildForestTransformsFromLocalAngles(
            forest, centers, {axis: 'Z', angleByBoneId: targetAngles});
          const repeatedTransforms = deformation.buildForestTransformsFromLocalAngles(
            forest, centers, {axis: 'Z', angleByBoneId: targetAngles});
          const matrixDifference = (left, right) => Math.max(...left.elements.map(
            (value, index) => Math.abs(value - right.elements[index])));
          const branchOnly = deformation.buildForestTransformsFromLocalAngles(
            forest, centers, {axis: 'Z', angleByBoneId: new Map([[2, .2]])});
          return {
            zero: [...physics.physicsAngleMap(zeroState).values()],
            targetAngles: [...targetAngles.entries()],
            underPeak: peak, underFinal, target,
            settled, criticalPeak,
            kick,
            reset: [...physics.physicsAngleMap(reset).entries()],
            fixedEqual: [...fixedA.joints.entries()].every(([boneId, joint]) => {
              const other = fixedB.joints.get(boneId);
              return Math.abs(joint.angle - other.angle) < 1e-9
                && Math.abs(joint.angularVelocity - other.angularVelocity) < 1e-9;
            }),
            rootIdentity: dynamicTransforms.get(0).equals(
              new (await import('three')).Matrix4()),
            repeatedDifference: Math.max(...[1, 2, 3].map(boneId =>
              matrixDifference(dynamicTransforms.get(boneId),
                repeatedTransforms.get(boneId)))),
            branchDifference: matrixDifference(branchOnly.get(2),
              dynamicTransforms.get(2)),
            siblingUnchanged: branchOnly.get(3).equals(
              new (await import('three')).Matrix4()),
          };
        }""")
        assert result["zero"] == pytest.approx([0, 0, 0])
        assert [entry[0] for entry in result["targetAngles"]] == [1, 2, 3]
        assert [entry[1] for entry in result["targetAngles"]] == pytest.approx([
            math.radians(20), math.radians(20), math.radians(20)])
        assert result["underPeak"] > math.radians(20)
        assert result["underFinal"] != pytest.approx(0)
        assert result["settled"]
        assert result["criticalPeak"] <= math.radians(20) + 1e-6
        assert [entry[0] for entry in result["kick"]] == [1, 2, 3]
        assert [entry[1] for entry in result["kick"]] == pytest.approx([
            1, 2, 2])
        assert [entry[0] for entry in result["reset"]] == [1, 2, 3]
        assert [entry[1] for entry in result["reset"]] == pytest.approx([
            0, 0, 0])
        assert result["fixedEqual"]
        assert result["rootIdentity"]
        assert result["repeatedDifference"] < .001
        assert result["branchDifference"] > .01
        assert result["siblingUnchanged"]
    finally:
        context.close()


def test_skinning_joint_limits_cover_depth_safety_and_motion_paths(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"JointLimits": _payload("JointLimits")})
    try:
        result = page.evaluate("""async () => {
          const physics = await import('./js/mesh/weight-physics.js');
          const forest = {
            components: [
              {componentId: 4, rootId: 0, nodeIds: [0, 1, 2, 3, 4],
                maxDepth: 4,
                depthById: {0: 0, 1: 1, 2: 2, 3: 3, 4: 4}},
              {componentId: 9, rootId: 10, nodeIds: [10, 11, 12],
                maxDepth: 2,
                depthById: {10: 0, 11: 1, 12: 2}},
            ],
          };
          const totalLimit = Math.PI * 20 / 180;
          const built = physics.buildPhysicsJointLimits(forest, totalLimit);
          const radians = degrees => Math.PI * degrees / 180;
          const positive = physics.initializePhysicsState(forest);
          positive.joints.get(1).angle = radians(8);
          positive.joints.get(1).angularVelocity = 3;
          physics.applyPhysicsJointLimits(positive, built.limitByBoneId);

          const inward = physics.initializePhysicsState(forest);
          inward.joints.get(1).angle = radians(5);
          inward.joints.get(1).angularVelocity = -3;
          physics.applyPhysicsJointLimits(inward, built.limitByBoneId);
          physics.stepSpringPhysics(inward, forest, 1 / 120, {
            targetAngleRadians: 0, frequencyHz: 2, dampingRatio: .35,
            jointLimitByBoneId: built.limitByBoneId,
          });

          const negative = physics.initializePhysicsState(forest);
          negative.joints.get(1).angle = radians(-8);
          negative.joints.get(1).angularVelocity = -3;
          physics.applyPhysicsJointLimits(negative, built.limitByBoneId);

          const locked = physics.initializePhysicsState(forest);
          locked.joints.get(1).angle = radians(8);
          locked.joints.get(1).angularVelocity = 3;
          const zeroLimits = new Map(built.limitByBoneId);
          zeroLimits.set(1, 0);
          physics.applyPhysicsJointLimits(locked, zeroLimits);

          const targetState = physics.initializePhysicsState(forest);
          for (let index = 0; index < 1800; index += 1) {
            physics.stepSpringPhysics(targetState, forest, 1 / 120, {
              targetAngleRadians: radians(80), frequencyHz: 2,
              dampingRatio: .35, jointLimitByBoneId: built.limitByBoneId,
            });
          }
          const targetEquilibrium = physics.buildPhysicsEquilibriumAngles(
            forest, radians(80), 2, null, built.limitByBoneId);
          const targetSettled = physics.isPhysicsSettled(
            targetState, forest, radians(80), {
              frequencyHz: 2, jointLimitByBoneId: built.limitByBoneId,
            });

          const gravityState = physics.initializePhysicsState(forest);
          const gravity = new Map([[1, 100], [2, 100], [3, 100], [4, 100],
            [11, 100], [12, 100]]);
          for (let index = 0; index < 1800; index += 1) {
            physics.stepSpringPhysics(gravityState, forest, 1 / 120, {
              targetAngleRadians: 0, frequencyHz: 2, dampingRatio: .35,
              externalAngularAccelerationByBoneId: gravity,
              jointLimitByBoneId: built.limitByBoneId,
            });
          }
          const gravitySettled = physics.isPhysicsSettled(
            gravityState, forest, 0, {
              frequencyHz: 2,
              externalAngularAccelerationByBoneId: gravity,
              jointLimitByBoneId: built.limitByBoneId,
            });

          const direct = physics.initializePhysicsState(forest);
          physics.applyReferenceFrameAngularDelta(
            direct, forest, radians(-90), 1, built.limitByBoneId);
          const translation = physics.initializePhysicsState(forest);
          const centers = new Map([
            [0, [0, 0, 0]], [1, [1, 0, 0]], [2, [2, 0, 0]],
            [3, [3, 0, 0]], [4, [4, 0, 0]],
            [10, [0, 0, 0]], [11, [1, 0, 0]], [12, [2, 0, 0]],
          ]);
          physics.applyReferenceFrameTranslationDelta(
            translation, forest, centers, [100, 0, 0], 'Z', 1, null,
            built.limitByBoneId);
          const kicked = physics.initializePhysicsState(forest);
          kicked.joints.forEach((joint, boneId) => {
            joint.angle = built.limitByBoneId.get(boneId);
          });
          physics.applyPhysicsKick(kicked, forest, 100, built.limitByBoneId);
          const safe = physics.initializePhysicsState(forest);
          safe.joints.get(1).angle = radians(120);
          physics.applyPhysicsJointLimits(safe, null);
          const disabled = physics.initializePhysicsState(forest);
          const enabled = physics.initializePhysicsState(forest);
          physics.stepSpringPhysics(disabled, forest, 1 / 120, {
            targetAngleRadians: radians(10), frequencyHz: 2, dampingRatio: .35,
          });
          physics.stepSpringPhysics(enabled, forest, 1 / 120, {
            targetAngleRadians: radians(10), frequencyHz: 2, dampingRatio: .35,
            jointLimitByBoneId: new Map(),
          });
          return {
            map: [...built.limitByBoneId.entries()],
            diagnostics: built.diagnostics,
            positive: [positive.joints.get(1).angle,
              positive.joints.get(1).angularVelocity],
            inward: [inward.joints.get(1).angle,
              inward.joints.get(1).angularVelocity],
            negative: [negative.joints.get(1).angle,
              negative.joints.get(1).angularVelocity],
            locked: [locked.joints.get(1).angle,
              locked.joints.get(1).angularVelocity],
            target: {
              angles: [...targetState.joints.entries()].map(([id, joint]) =>
                [id, joint.angle]),
              equilibrium: [...targetEquilibrium.entries()],
              settled: targetSettled,
            },
            gravity: {
              angles: [...gravityState.joints.entries()].map(([id, joint]) =>
                [id, joint.angle]),
              settled: gravitySettled,
            },
            direct: [...direct.joints.values()].map(joint => joint.angle),
            translation: [...translation.joints.values()].map(
              joint => joint.angle),
            kick: [...kicked.joints.values()].map(joint =>
              joint.angularVelocity),
            safe: safe.joints.get(1).angle,
            disabled: [...disabled.joints.entries()].map(([id, joint]) =>
              [id, joint.angle, joint.angularVelocity]),
            enabled: [...enabled.joints.entries()].map(([id, joint]) =>
              [id, joint.angle, joint.angularVelocity]),
          };
        }""")
        assert [entry[0] for entry in result["map"]] == [1, 2, 3, 4, 11, 12]
        assert [entry[1] for entry in result["map"]] == pytest.approx([
            math.radians(5), math.radians(5), math.radians(5), math.radians(5),
            math.radians(10), math.radians(10)])
        assert result["diagnostics"]["jointCount"] == 6
        assert result["diagnostics"]["components"][0]["maxDepth"] == 4
        assert result["positive"] == pytest.approx([math.radians(5), 0])
        assert result["inward"][0] < math.radians(5)
        assert result["inward"][1] < 0
        assert result["negative"] == pytest.approx([math.radians(-5), 0])
        assert result["locked"] == [0, 0]
        assert result["target"]["settled"]
        assert [entry[1] for entry in result["target"]["equilibrium"]] == pytest.approx([
            math.radians(5), math.radians(5), math.radians(5), math.radians(5),
            math.radians(10), math.radians(10)])
        assert max(abs(entry[1]) for entry in result["target"]["angles"]) <= math.radians(10) + 1e-6
        assert result["gravity"]["settled"]
        assert max(abs(entry[1]) for entry in result["gravity"]["angles"]) <= math.radians(10) + 1e-6
        assert max(abs(angle) for angle in result["direct"]) <= math.radians(10) + 1e-6
        assert max(abs(angle) for angle in result["translation"]) <= math.radians(10) + 1e-6
        assert max(abs(velocity) for velocity in result["kick"]) <= 1e-9
        assert result["safe"] == pytest.approx(math.radians(90))
        assert result["disabled"] == result["enabled"]
    finally:
        context.close()


def test_skinning_gravity_solver_builds_safe_component_accelerations(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"GravitySolver": _payload("GravitySolver")})
    try:
        result = page.evaluate("""async () => {
          const physics = await import('./js/mesh/weight-physics.js');
          const forest = {
            components: [
              {componentId: 4, rootId: 0, nodeIds: [0, 1, 2], maxDepth: 2,
                depthById: {0: 0, 1: 1, 2: 2}},
              {componentId: 9, rootId: 10, nodeIds: [10, 11], maxDepth: 1,
                depthById: {10: 0, 11: 1}},
            ],
          };
          const centers = new Map([
            [0, [0, 0, 0]], [1, [1, 0, 0]], [2, [2, 0, 0]],
            [10, [0, 0, 2]], [11, [0, 0, 3]],
          ]);
          const gravity = physics.buildGravityAngularAccelerations(
            forest, centers, [0, -1, 0], 'Z',
            {referenceRadius: 2, gravityScale: 1});
          const reverse = physics.buildGravityAngularAccelerations(
            {components: [{componentId: 1, rootId: 0, nodeIds: [0, 1],
              maxDepth: 1, depthById: {0: 0, 1: 1}}]},
            new Map([[0, [0, 0, 0]], [1, [-1, 0, 0]]]),
            [0, -1, 0], 'Z', {referenceRadius: 1, gravityScale: 1});
          const parallel = physics.buildGravityAngularAccelerations(
            {components: [{rootId: 0, nodeIds: [0, 1], maxDepth: 1,
              depthById: {0: 0, 1: 1}}]},
            new Map([[0, [0, 0, 0]], [1, [0, 1, 0]]]),
            [0, -1, 0], 'Z', {referenceRadius: 1, gravityScale: 1});
          const axisParallel = physics.buildGravityAngularAccelerations(
            {components: [{rootId: 0, nodeIds: [0, 1], maxDepth: 1,
              depthById: {0: 0, 1: 1}}]},
            new Map([[0, [0, 0, 0]], [1, [0, 0, 1]]]),
            [0, -1, 0], 'Z', {referenceRadius: 1, gravityScale: 1});
          const short = physics.buildGravityAngularAccelerations(
            {components: [{rootId: 0, nodeIds: [0, 1], maxDepth: 1,
              depthById: {0: 0, 1: 1}}]},
            new Map([[0, [0, 0, 0]], [1, [.01, 0, 0]]]),
            [0, -1, 0], 'Z', {referenceRadius: 2, gravityScale: 1});
          const scaled = physics.buildGravityAngularAccelerations(
            forest, new Map([
              [0, [0, 0, 0]], [1, [100, 0, 0]], [2, [200, 0, 0]],
              [10, [0, 0, 200]], [11, [0, 0, 300]],
            ]), [0, -1, 0], 'Z',
            {referenceRadius: 200, gravityScale: 1});

          const external = new Map([[1, .8], [2, .8], [11, -.4]]);
          const springState = physics.initializePhysicsState(forest);
          for (let index = 0; index < 1800; index += 1) {
            physics.stepSpringPhysics(springState, forest, 1 / 120, {
              targetAngleRadians: .2, frequencyHz: 2, dampingRatio: .35,
              externalAngularAccelerationByBoneId: external,
            });
          }
          const omegaSquared = (2 * Math.PI * 2) ** 2;
          const equilibrium = physics.buildPhysicsEquilibriumAngles(
            forest, .2, 2, external);
          const custom = new Map([[1, .1], [2, .1], [11, 0]]);
          const customState = physics.initializePhysicsState(forest);
          physics.stepSpringPhysics(customState, forest, 1 / 120, {
            angleByBoneId: custom, frequencyHz: 2, dampingRatio: .35,
            externalAngularAccelerationByBoneId: external,
          });
          return {
            gravityMap: [...gravity.accelerationByBoneId.entries()],
            gravityDiagnostics: gravity.diagnostics,
            reverse: reverse.accelerationByBoneId.get(1),
            parallel: parallel.accelerationByBoneId.get(1),
            axisParallel: axisParallel.accelerationByBoneId.get(1),
            short: {
              value: short.accelerationByBoneId.get(1),
              details: short.diagnostics.components[0],
            },
            scaled: [...scaled.accelerationByBoneId.entries()],
            roots: [gravity.accelerationByBoneId.has(0),
              gravity.accelerationByBoneId.has(10)],
            springAngles: [...springState.joints.entries()].map(([id, joint]) =>
              [id, joint.angle]),
            equilibrium: [...equilibrium.entries()],
            settled: physics.isPhysicsSettled(
              springState, forest, .2, {frequencyHz: 2,
                externalAngularAccelerationByBoneId: external}),
            zeroFrequencySettled: physics.isPhysicsSettled(
              physics.initializePhysicsState(forest), forest, 0,
              {frequencyHz: 0, externalAngularAccelerationByBoneId:
                new Map([[1, 1]])}),
            customAfterStep: [...customState.joints.entries()].map(([id, joint]) =>
              [id, joint.angle]),
            customTarget: custom.get(1) + external.get(1) / omegaSquared,
          };
        }""")
        component = result["gravityDiagnostics"]["components"][0]
        assert result["gravityDiagnostics"]["componentCount"] == 2
        assert result["gravityDiagnostics"]["activeComponentCount"] == 1
        assert result["gravityDiagnostics"]["clampedComponentCount"] == 1
        assert result["roots"] == [False, False]
        assert [entry[0] for entry in result["gravityMap"]] == [1, 2, 11]
        assert result["gravityMap"][0][1] < 0
        assert result["gravityMap"][1][1] == pytest.approx(
            result["gravityMap"][0][1])
        assert result["reverse"] > 0
        assert result["parallel"] == pytest.approx(0)
        assert result["axisParallel"] == pytest.approx(0)
        assert result["short"]["details"]["clamped"]
        assert result["short"]["details"]["effectiveLeverLength"] == pytest.approx(.3)
        assert math.isfinite(result["short"]["value"])
        assert abs(result["short"]["value"]) < 10
        assert [entry[0] for entry in result["scaled"]] == [1, 2, 11]
        assert [entry[1] for entry in result["scaled"]] == pytest.approx(
            [entry[1] for entry in result["gravityMap"]])
        assert component["totalAngularAcceleration"] == pytest.approx(
            component["localAngularAcceleration"] * 2)
        assert [entry[0] for entry in result["equilibrium"]] == [1, 2, 11]
        assert [entry[1] for entry in result["equilibrium"]] == pytest.approx([
            .1 + .8 / (2 * math.pi * 2) ** 2,
            .1 + .8 / (2 * math.pi * 2) ** 2,
            .2 - .4 / (2 * math.pi * 2) ** 2,
        ])
        assert result["settled"]
        assert not result["zeroFrequencySettled"]
        assert result["customAfterStep"][0][0] == 1
        assert result["customAfterStep"][0][1] != pytest.approx(
            result["customTarget"])
    finally:
        context.close()


def test_skinning_angular_motion_helpers_cover_projection_and_lag(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"AngularMotion": _payload("AngularMotion")})
    try:
        result = page.evaluate("""async () => {
          const THREE = await import('three');
          const experiment = await import('./js/mesh/weight-experiment.js');
          const physics = await import('./js/mesh/weight-physics.js');
          const identity = new THREE.Quaternion();
          const y30 = new THREE.Quaternion().setFromAxisAngle(
            new THREE.Vector3(0, 1, 0), Math.PI / 6);
          const negativeY20 = new THREE.Quaternion().setFromAxisAngle(
            new THREE.Vector3(0, 1, 0), -Math.PI / 9);
          const y30Negative = new THREE.Quaternion(
            -y30.x, -y30.y, -y30.z, -y30.w);
          const previous = new THREE.Quaternion().setFromEuler(
            new THREE.Euler(Math.PI / 6, 0, Math.PI / 5, 'XYZ'));
          const worldYTurn = new THREE.Quaternion().setFromAxisAngle(
            new THREE.Vector3(0, 1, 0), Math.PI / 6);
          const current = worldYTurn.clone().multiply(previous);
          const expectedLocalY = new THREE.Vector3(0, 1, 0)
            .applyQuaternion(previous.clone().invert());
          const forest = {
            primaryRootId: 0,
            components: [
              {rootId: 0, nodeIds: [0, 1, 2], maxDepth: 2,
                depthById: {0: 0, 1: 1, 2: 2}},
              {rootId: 5, nodeIds: [5, 6], maxDepth: 1,
                depthById: {5: 0, 6: 1}},
            ],
          };
          const lagState = physics.initializePhysicsState(forest);
          physics.applyReferenceFrameAngularDelta(
            lagState, forest, Math.PI * 40 / 180, .5);
          const zeroResponse = physics.initializePhysicsState(forest);
          physics.applyReferenceFrameAngularDelta(
            zeroResponse, forest, Math.PI * 40 / 180, 0);
          const opposite = physics.initializePhysicsState(forest);
          physics.applyReferenceFrameAngularDelta(
            opposite, forest, Math.PI * 10 / 180, .5);
          const firstOpposite = opposite.joints.get(1).angle;
          physics.applyReferenceFrameAngularDelta(
            opposite, forest, -Math.PI * 10 / 180, .5);
          const secondOpposite = opposite.joints.get(1).angle - firstOpposite;

          const recovery = physics.initializePhysicsState(forest);
          physics.applyReferenceFrameAngularDelta(
            recovery, forest, Math.PI * 30 / 180, 1);
          const initialLag = recovery.joints.get(1).angle;
          let overshoot = false;
          for (let index = 0; index < 1200; index += 1) {
            physics.stepSpringPhysics(recovery, forest, 1 / 120, {
              targetAngleRadians: 0, frequencyHz: 2, dampingRatio: .35,
            });
            if (recovery.joints.get(1).angle > 0) overshoot = true;
          }
          const settled = physics.isPhysicsSettled(
            recovery, forest, 0, {angleTolerance: .002, velocityTolerance: .002});

          const biased = physics.initializePhysicsState(forest);
          const target = Math.PI * 40 / 180;
          for (let index = 0; index < 1200; index += 1) {
            physics.stepSpringPhysics(biased, forest, 1 / 120, {
              targetAngleRadians: target, frequencyHz: 2, dampingRatio: .35,
            });
          }
          physics.applyReferenceFrameAngularDelta(
            biased, forest, Math.PI * 30 / 180, .5);
          for (let index = 0; index < 1200; index += 1) {
            physics.stepSpringPhysics(biased, forest, 1 / 120, {
              targetAngleRadians: target, frequencyHz: 2, dampingRatio: .35,
            });
          }
          return {
            sameY: experiment.projectQuaternionDeltaOntoAxis(
              identity, y30, 'Y'),
            sameX: experiment.projectQuaternionDeltaOntoAxis(
              identity, y30, 'X'),
            negativeY: experiment.projectQuaternionDeltaOntoAxis(
              identity, negativeY20, 'Y'),
            signEquivalent: experiment.projectQuaternionDeltaOntoAxis(
              identity, y30Negative, 'Y'),
            localY: experiment.projectQuaternionDeltaOntoAxis(
              previous, current, 'Y'),
            expectedLocalY: Math.PI / 6 * expectedLocalY.y,
            lagAngles: [...lagState.joints.entries()].map(([id, joint]) =>
              [id, joint.angle]),
            zeroAngles: [...zeroResponse.joints.values()].map(joint => joint.angle),
            firstOpposite, secondOpposite,
            initialLag, overshoot, settled,
            biased: [...biased.joints.values()].map(joint => joint.angle),
          };
        }""")
        assert result["sameY"] == pytest.approx(math.radians(30))
        assert result["sameX"] == pytest.approx(0)
        assert result["negativeY"] == pytest.approx(math.radians(-20))
        assert result["signEquivalent"] == pytest.approx(math.radians(30))
        assert result["localY"] == pytest.approx(result["expectedLocalY"])
        assert [entry[0] for entry in result["lagAngles"]] == [1, 2, 6]
        assert [entry[1] for entry in result["lagAngles"]] == pytest.approx([
            math.radians(-10), math.radians(-10), math.radians(-20)])
        assert result["zeroAngles"] == pytest.approx([0, 0, 0])
        assert result["firstOpposite"] < 0
        assert result["secondOpposite"] > 0
        assert result["initialLag"] < 0
        assert result["overshoot"]
        assert result["settled"]
        assert result["biased"] == pytest.approx([
            math.radians(20), math.radians(20), math.radians(40)], abs=.002)
    finally:
        context.close()


def test_skinning_translation_motion_solver_uses_projected_component_levers(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"TranslationMotion": _payload("TranslationMotion")})
    try:
        result = page.evaluate("""async () => {
          const physics = await import('./js/mesh/weight-physics.js');
          const forest = {
            components: [
              {rootId: 0, nodeIds: [0, 1, 2], maxDepth: 2,
                depthById: {0: 0, 1: 1, 2: 2}},
              {rootId: 5, nodeIds: [5, 6], maxDepth: 1,
                depthById: {5: 0, 6: 1}},
            ],
          };
          const centers = new Map([
            [0, [0, 0, 0]], [1, [0, .5, 0]], [2, [0, 1, 0]],
            [5, [2, 0, 0]], [6, [2, 1, 0]],
          ]);
          const solve = (delta, response = .5) => {
            const state = physics.initializePhysicsState(forest);
            const diagnostics = {};
            physics.applyReferenceFrameTranslationDelta(
              state, forest, centers, delta, 'Z', response, diagnostics);
            return {
              angles: [...state.joints.values()].map(joint => joint.angle),
              lag: diagnostics.maxAbsLag,
            };
          };
          const velocityState = physics.initializePhysicsState(forest);
          velocityState.joints.get(1).angularVelocity = 1.25;
          physics.applyReferenceFrameTranslationDelta(
            velocityState, forest, centers, [.1, 0, 0], 'Z', .5);
          const degenerateCenters = new Map([
            [0, [0, 0, 0]], [1, [0, 0, 0]], [2, [0, 0, 0]],
          ]);
          const degenerateState = physics.initializePhysicsState({
            components: [{rootId: 0, nodeIds: [0, 1, 2], maxDepth: 2,
              depthById: {0: 0, 1: 1, 2: 2}}],
          });
          physics.applyReferenceFrameTranslationDelta(
            degenerateState, forest, degenerateCenters, [.1, 0, 0], 'Z', 1);
          return {
            plus: solve([.1, 0, 0]),
            minus: solve([-.1, 0, 0]),
            parallel: solve([0, .1, 0]),
            alongAxis: solve([0, 0, .1]),
            zero: solve([.1, 0, 0], 0),
            half: solve([.1, 0, 0], .5),
            full: solve([.1, 0, 0], 1),
            velocity: velocityState.joints.get(1).angularVelocity,
            roots: [velocityState.joints.has(0), velocityState.joints.has(5)],
            degenerate: [...degenerateState.joints.values()].map(joint => ({
              angle: joint.angle, angularVelocity: joint.angularVelocity,
            })),
          };
        }""")
        assert result["plus"]["lag"] > 0
        assert result["minus"]["lag"] > 0
        assert result["plus"]["angles"] == pytest.approx([
            result["plus"]["angles"][0], result["plus"]["angles"][0],
            result["plus"]["angles"][0] * 2])
        assert result["plus"]["angles"][0] == pytest.approx(
            result["plus"]["lag"] / 2)
        assert result["minus"]["angles"] == pytest.approx([
            -result["minus"]["lag"] / 2, -result["minus"]["lag"] / 2,
            -result["minus"]["lag"]])
        assert result["parallel"]["lag"] == pytest.approx(0)
        assert result["alongAxis"]["lag"] == pytest.approx(0)
        assert result["zero"]["lag"] == pytest.approx(0)
        assert result["half"]["lag"] < result["full"]["lag"]
        assert result["velocity"] == pytest.approx(1.25)
        assert result["roots"] == [False, False]
        assert result["degenerate"] == pytest.approx([
            {"angle": 0, "angularVelocity": 0},
            {"angle": 0, "angularVelocity": 0},
        ])
    finally:
        context.close()


def test_skinning_linear_velocity_impulse_preserves_angles_and_phase(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"LinearVelocity": _payload("LinearVelocity")})
    try:
        result = page.evaluate("""async () => {
          const physics = await import('./js/mesh/weight-physics.js');
          const forest = {
            components: [{rootId: 0, nodeIds: [0, 1, 2], maxDepth: 2,
              depthById: {0: 0, 1: 1, 2: 2}}],
          };
          const centers = new Map([
            [0, [0, 0, 0]], [1, [0, .5, 0]], [2, [0, 1, 0]],
          ]);
          const solve = (delta, response = .5) => {
            const state = physics.initializePhysicsState(forest);
            state.joints.get(1).angle = .3;
            state.joints.get(1).angularVelocity = .4;
            const diagnostics = {};
            physics.applyReferenceFrameLinearVelocityDelta(
              state, forest, centers, delta, 'Z', response, diagnostics);
            return {
              angles: [...state.joints.values()].map(joint => joint.angle),
              velocities: [...state.joints.values()]
                .map(joint => joint.angularVelocity),
              impulse: diagnostics.maxAbsDeltaOmega,
            };
          };
          const constant = physics.initializePhysicsState(forest);
          physics.applyReferenceFrameLinearVelocityDelta(
            constant, forest, centers, [.2, 0, 0], 'Z', .5);
          const afterAcceleration = [...constant.joints.values()]
            .map(joint => joint.angularVelocity);
          physics.applyReferenceFrameLinearVelocityDelta(
            constant, forest, centers, [0, 0, 0], 'Z', .5);
          const afterConstant = [...constant.joints.values()]
            .map(joint => joint.angularVelocity);
          const degenerateForest = {
            components: [{rootId: 0, nodeIds: [0, 1], maxDepth: 1,
              depthById: {0: 0, 1: 1}}],
          };
          const degenerate = physics.initializePhysicsState(degenerateForest);
          physics.applyReferenceFrameLinearVelocityDelta(
            degenerate, degenerateForest,
            new Map([[0, [0, 0, 0]], [1, [0, 0, 0]]]),
            [.2, 0, 0], 'Z', 1);
          return {
            lever: physics.representativeComponentLever(
              forest.components[0], centers),
            plus: solve([.2, 0, 0]),
            minus: solve([-.2, 0, 0]),
            parallel: solve([0, .2, 0]),
            alongAxis: solve([0, 0, .2]),
            zeroResponse: solve([.2, 0, 0], 0),
            fullResponse: solve([.2, 0, 0], 1),
            afterAcceleration, afterConstant,
            degenerate: [...degenerate.joints.values()]
              .map(joint => [joint.angle, joint.angularVelocity]),
          };
        }""")
        assert result["lever"] == pytest.approx([0, 1, 0])
        assert result["plus"]["impulse"] == pytest.approx(.1)
        assert result["plus"]["angles"] == pytest.approx([.3, 0])
        assert result["plus"]["velocities"] == pytest.approx([.45, .05])
        assert result["minus"]["impulse"] == pytest.approx(.1)
        assert result["minus"]["velocities"] == pytest.approx([.35, -.05])
        assert result["parallel"]["impulse"] == pytest.approx(0)
        assert result["alongAxis"]["impulse"] == pytest.approx(0)
        assert result["zeroResponse"]["impulse"] == pytest.approx(0)
        assert result["fullResponse"]["impulse"] == pytest.approx(.2)
        assert result["afterAcceleration"] == pytest.approx([.05, .05])
        assert result["afterConstant"] == pytest.approx(result["afterAcceleration"])
        assert result["degenerate"][0] == pytest.approx([0, 0])
    finally:
        context.close()




def test_skinning_physics_lifecycle_sleeps_switches_and_disposes(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url,
        {"PhysicsLifecycle": _payload("PhysicsLifecycle")})
    try:
        _open(page, "PhysicsLifecycle")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        result = page.evaluate("""async () => {
          const mesh = window.modViewer.activeMeshes[0];
          const bytes = new Uint8Array(48);
          new Uint32Array(bytes.buffer).set([0, 1, 1, 2, 0, 2]);
          new Float32Array(bytes.buffer, 24).set([.8, .2, .7, .3, .6, .4]);
          const url = URL.createObjectURL(new Blob([bytes]));
          window.pywebview.api.get_skinning_preview = async () => ({
            status: 'ok', vertex_count: 3, influence_count: 2,
            bone_ids: [0, 1, 2], encoding: 'test',
            data: {
              url, length: 48,
              indices: {offset: 0, length: 24, type: 'u32'},
              weights: {offset: 24, length: 24, type: 'f32'},
            }, diagnostics: {},
          });
          const experiment = await import('./js/mesh/weight-experiment.js');
          const {setControlValue} = await import('./js/editing/control-state.js');
          const {refreshMeshes} = await import('./js/mesh/mesh-state.js');
          await experiment.loadSkinningWeights(mesh);
          experiment.ensureCandidateForest(mesh);
          const queuedFrames = [];
          const originalRequestAnimationFrame = window.requestAnimationFrame;
          const originalCancelAnimationFrame = window.cancelAnimationFrame;
          window.requestAnimationFrame = callback => {
            queuedFrames.push(callback);
            return callback;
          };
          window.cancelAnimationFrame = callback => {
            const index = queuedFrames.indexOf(callback);
            if (index >= 0) queuedFrames.splice(index, 1);
          };
          const runFrame = timestamp => {
            const callback = queuedFrames.shift();
            if (!callback) throw new Error('Expected a queued physics frame.');
            callback(timestamp);
          };
          experiment.setPhysicsEnabled(mesh, true);
          const enabled = experiment.getSkinningState(mesh);
          const enabledState = {
            mode: enabled.deformationMode,
            enabled: enabled.physicsEnabled,
            jointCount: enabled.physicsState.joints.size,
            scheduled: experiment.isPhysicsScheduled(mesh),
          };
          runFrame(0);
          runFrame(16.7);
          const sleeping = experiment.getSkinningState(mesh);
          const sleepingState = {
            settled: sleeping.physicsSettled,
            scheduled: experiment.isPhysicsScheduled(mesh),
            target: sleeping.physicsTargetAngle,
            joints: [...sleeping.physicsState.joints.values()].map(joint => ({
              angle: joint.angle, angularVelocity: joint.angularVelocity,
            })),
          };

          const beforeTarget = [...mesh.geometry.attributes.position.array];
          experiment.setPhysicsTargetAngle(mesh, 30);
          runFrame(100);
          runFrame(116.7);
          const moving = experiment.getSkinningState(mesh);
          const movingPositions = [...mesh.geometry.attributes.position.array];
          const movingState = {
            target: moving.physicsTargetAngle,
            mode: moving.deformationMode,
            scheduled: experiment.isPhysicsScheduled(mesh),
            angle: moving.physicsState.joints.get(1).angle,
            changed: movingPositions.some((value, index) =>
              Math.abs(value - beforeTarget[index]) > 1e-5),
          };

          experiment.ensureCandidateForest(mesh);
          experiment.setPhysicsEnabled(mesh, true);
          experiment.setPhysicsTargetAngle(mesh, 20);
          runFrame(200);
          runFrame(216.7);
          experiment.resetPhysicsMotion(mesh);
          const reset = experiment.getSkinningState(mesh);
          const resetState = {
            enabled: reset.physicsEnabled,
            mode: reset.deformationMode,
            target: reset.physicsTargetAngle,
            settled: reset.physicsSettled,
            scheduled: experiment.isPhysicsScheduled(mesh),
            angles: [...reset.physicsState.joints.values()]
              .map(joint => joint.angle),
            positions: [...mesh.geometry.attributes.position.array],
          };

          experiment.setPhysicsTargetAngle(mesh, 20);
          const scheduledBeforeShape = experiment.isPhysicsScheduled(mesh);
          setControlValue('shape', '1');
          refreshMeshes();
          const afterShape = experiment.getSkinningState(mesh);
          window.requestAnimationFrame = originalRequestAnimationFrame;
          window.cancelAnimationFrame = originalCancelAnimationFrame;
          URL.revokeObjectURL(url);
          return {
            enabledState, sleepingState, movingState, resetState,
            scheduledBeforeShape,
            afterShape: afterShape === null,
            scheduledAfterShape: experiment.isPhysicsScheduled(mesh),
          };
        }""")
        assert result["enabledState"] == {
            "mode": "physics", "enabled": True, "jointCount": 2,
            "scheduled": True}
        assert result["sleepingState"]["settled"]
        assert not result["sleepingState"]["scheduled"]
        assert result["sleepingState"]["target"] == 0
        assert [joint["angle"] for joint in result["sleepingState"]["joints"]] == pytest.approx([
            0, 0])
        assert result["movingState"]["target"] == 30
        assert result["movingState"]["mode"] == "physics"
        assert result["movingState"]["scheduled"]
        assert result["movingState"]["angle"] != pytest.approx(0)
        assert result["movingState"]["changed"]
        assert result["resetState"]["enabled"]
        assert result["resetState"]["mode"] == "physics"
        assert result["resetState"]["target"] == 0
        assert result["resetState"]["settled"]
        assert not result["resetState"]["scheduled"]
        assert result["resetState"]["angles"] == pytest.approx([0, 0])
        assert result["scheduledBeforeShape"]
        assert result["afterShape"]
        assert not result["scheduledAfterShape"]
    finally:
        context.close()


def test_skinning_load_is_invalidated_by_shape_change(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"SkinningLoadRace": _payload("SkinningLoadRace")})
    try:
        _open(page, "SkinningLoadRace")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        result = page.evaluate("""async () => {
          const mesh = window.modViewer.activeMeshes[0];
          const bytes = new Uint8Array(48);
          new Uint32Array(bytes.buffer).set([0, 1, 1, 2, 0, 2]);
          new Float32Array(bytes.buffer, 24).set([.8, .2, .7, .3, .6, .4]);
          const url = URL.createObjectURL(new Blob([bytes]));
          let releasePreview;
          const previewPending = new Promise(resolve => {
            releasePreview = resolve;
          });
          window.pywebview.api.get_skinning_preview = async () => previewPending;
          const experiment = await import('./js/mesh/weight-experiment.js');
          const {setControlValue} = await import('./js/editing/control-state.js');
          const {refreshMeshes} = await import('./js/mesh/mesh-state.js');
          const loadPromise = experiment.loadSkinningWeights(mesh);
          const loading = experiment.getSkinningState(mesh)?.loading;
          setControlValue('shape', '1');
          refreshMeshes();
          const invalidated = experiment.getSkinningState(mesh) === null;
          releasePreview({
            status: 'ok', vertex_count: 3, influence_count: 2,
            bone_ids: [0, 1, 2], encoding: 'test',
            data: {
              url, length: 48,
              indices: {offset: 0, length: 24, type: 'u32'},
              weights: {offset: 24, length: 24, type: 'f32'},
            }, diagnostics: {},
          });
          const error = await loadPromise.then(
            () => null, failure => failure.message);
          URL.revokeObjectURL(url);
          return {
            loading, invalidated, error,
            stateAfterLoad: experiment.getSkinningState(mesh),
          };
        }""")
        assert result["loading"]
        assert result["invalidated"]
        assert result["error"] == "The skin-weight experiment was reset."
        assert result["stateAfterLoad"] is None
    finally:
        context.close()


def test_skinning_angular_motion_follows_model_turn_and_ignores_camera(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"AngularLifecycle": _payload("AngularLifecycle")})
    try:
        _open(page, "AngularLifecycle")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        result = page.evaluate("""async () => {
          const mesh = window.modViewer.activeMeshes[0];
          const bytes = new Uint8Array(48);
          new Uint32Array(bytes.buffer).set([0, 1, 1, 2, 0, 2]);
          new Float32Array(bytes.buffer, 24).set([.8, .2, .7, .3, .6, .4]);
          const url = URL.createObjectURL(new Blob([bytes]));
          let previewCalls = 0;
          window.pywebview.api.get_skinning_preview = async () => {
            previewCalls += 1;
            return {
              status: 'ok', vertex_count: 3, influence_count: 2,
              bone_ids: [0, 1, 2], encoding: 'test',
              data: {
                url, length: 48,
                indices: {offset: 0, length: 24, type: 'u32'},
                weights: {offset: 24, length: 24, type: 'f32'},
              }, diagnostics: {},
            };
          };
          const experiment = await import('./js/mesh/weight-experiment.js');
          await experiment.loadSkinningWeights(mesh);
          experiment.ensureCandidateForest(mesh);
          previewCalls = 0;

          const queuedFrames = [];
          const originalRequestAnimationFrame = window.requestAnimationFrame;
          const originalCancelAnimationFrame = window.cancelAnimationFrame;
          window.requestAnimationFrame = callback => {
            queuedFrames.push(callback);
            return callback;
          };
          window.cancelAnimationFrame = callback => {
            const index = queuedFrames.indexOf(callback);
            if (index >= 0) queuedFrames.splice(index, 1);
          };
          const runFrame = timestamp => {
            const callbacks = queuedFrames.splice(0);
            if (!callbacks.length) throw new Error('Expected a queued frame.');
            callbacks.forEach(callback => callback(timestamp));
          };
          const settle = timestamp => {
            let current = timestamp;
            let frames = 0;
            while (queuedFrames.length && frames < 600) {
              runFrame(current);
              current += 16.7;
              frames += 1;
            }
            return {current, frames};
          };

          const motionEvents = [];
          window.addEventListener('mod-viewer-model-transform-changed', event => {
            motionEvents.push({
              reason: event.detail?.reason,
              includesMesh: event.detail?.meshes?.includes(mesh),
            });
          });
          experiment.setPhysicsAxis(mesh, 'Y');
          experiment.setPhysicsMotionStrength(mesh, .35);
          experiment.setPhysicsTargetAngle(mesh, 0);
          experiment.setPhysicsEnabled(mesh, true);
          runFrame(0);
          runFrame(16.7);
          const settledBeforeTurn = experiment.getSkinningState(mesh);
          const settledQuaternion = mesh.quaternion.clone();
          const settledPositions = [...mesh.geometry.attributes.position.array];

          document.querySelector('#camera-flip-btn').click();
          const afterTurn = experiment.getSkinningState(mesh);
          const firstLag = afterTurn.physicsState.joints.get(1)?.angle || 0;
          const firstTurn = {
            quaternionChanged: !mesh.quaternion.equals(settledQuaternion),
            eventCount: afterTurn.motionEventCount,
            event: motionEvents.at(-1),
            rootDelta: afterTurn.lastRootAngularDelta,
            projected: afterTurn.lastProjectedAngularDelta,
            angle: firstLag,
            mode: afterTurn.deformationMode,
            settled: afterTurn.physicsSettled,
            scheduled: experiment.isPhysicsScheduled(mesh),
            geometryChanged: mesh.geometry.attributes.position.array.some(
              (value, index) => Math.abs(value - settledPositions[index]) > 1e-5),
            previewCalls,
          };

          document.querySelector('#camera-flip-btn').click();
          const repeated = experiment.getSkinningState(mesh);
          const repeatedLag = repeated.physicsState.joints.get(1)?.angle || 0;
          const repeatedTurn = {
            eventCount: repeated.motionEventCount,
            moreLag: repeatedLag < firstLag,
            scheduled: experiment.isPhysicsScheduled(mesh),
          };
          let timing = settle(33.4);
          const afterTurns = experiment.getSkinningState(mesh);
          const settledAfterTurns = {
            settled: afterTurns.physicsSettled,
            scheduled: experiment.isPhysicsScheduled(mesh),
            angles: [...afterTurns.physicsState.joints.values()]
              .map(joint => joint.angle),
            velocities: [...afterTurns.physicsState.joints.values()]
              .map(joint => joint.angularVelocity),
            frames: timing.frames,
          };

          document.querySelector('#camera-reset-view-btn').click();
          const afterResetView = experiment.getSkinningState(mesh);
          const resetLag = afterResetView.physicsState.joints.get(1)?.angle || 0;
          const resetViewMotion = {
            eventCount: afterResetView.motionEventCount,
            positiveLag: resetLag > 0,
            reason: motionEvents.at(-1)?.reason,
            scheduled: experiment.isPhysicsScheduled(mesh),
            referenceMatches: afterResetView.physicsReferenceQuaternion
              .equals(mesh.quaternion),
          };
          timing = settle(timing.current);
          const afterResetSettle = experiment.getSkinningState(mesh);
          const settledAfterReset = {
            settled: afterResetSettle.physicsSettled,
            scheduled: experiment.isPhysicsScheduled(mesh),
            angles: [...afterResetSettle.physicsState.joints.values()]
              .map(joint => joint.angle),
          };

          const {camera} = await import('./js/scene/scene.js');
          const cameraBefore = camera.position.clone();
          camera.position.x += .2;
          camera.updateMatrixWorld();
          if (queuedFrames.length) runFrame(timing.current);
          const afterCamera = experiment.getSkinningState(mesh);
          const cameraIsolation = {
            cameraChanged: !camera.position.equals(cameraBefore),
            eventCount: afterCamera.motionEventCount,
            angle: afterCamera.physicsState.joints.get(1)?.angle || 0,
            settled: afterCamera.physicsSettled,
            scheduled: experiment.isPhysicsScheduled(mesh),
          };

          experiment.setPhysicsAxis(mesh, 'Y');
          document.querySelector('#camera-flip-horizontal-btn').click();
          const yAxisXTurn = experiment.getSkinningState(mesh);
          const yAxisIsolation = {
            eventCount: yAxisXTurn.motionEventCount,
            angle: yAxisXTurn.physicsState.joints.get(1)?.angle || 0,
            scheduled: experiment.isPhysicsScheduled(mesh),
          };
          document.querySelector('#camera-reset-view-btn').click();
          const afterHorizontalReset = experiment.getSkinningState(mesh);
          experiment.setPhysicsAxis(mesh, 'X');
          document.querySelector('#camera-flip-horizontal-btn').click();
          const xAxisXTurn = experiment.getSkinningState(mesh);
          const xAxisIsolation = {
            eventCount: xAxisXTurn.motionEventCount,
            angle: xAxisXTurn.physicsState.joints.get(1)?.angle || 0,
            scheduled: experiment.isPhysicsScheduled(mesh),
            referenceMatches: xAxisXTurn.physicsReferenceQuaternion
              .equals(mesh.quaternion),
          };
          experiment.resetPhysicsMotion(mesh);
          const resetMotionState = experiment.getSkinningState(mesh);
          const motionReset = {
            angle: resetMotionState.physicsState.joints.get(1)?.angle || 0,
            velocity: resetMotionState.physicsState.joints.get(1)
              ?.angularVelocity || 0,
            eventCount: resetMotionState.motionEventCount,
            scheduled: experiment.isPhysicsScheduled(mesh),
            referenceMatches: resetMotionState.physicsReferenceQuaternion
              .equals(mesh.quaternion),
          };
          const quaternionBeforeDisable = mesh.quaternion.clone();
          experiment.setPhysicsEnabled(mesh, false);
          const disabled = experiment.getSkinningState(mesh);
          const disabledState = {
            quaternionUnchanged: mesh.quaternion.equals(quaternionBeforeDisable),
            referenceCleared: disabled.physicsReferenceQuaternion === null,
            scheduled: experiment.isPhysicsScheduled(mesh),
          };
          window.requestAnimationFrame = originalRequestAnimationFrame;
          window.cancelAnimationFrame = originalCancelAnimationFrame;
          URL.revokeObjectURL(url);
          return {
            firstTurn, repeatedTurn, settledAfterTurns,
                resetViewMotion, settledAfterReset, cameraIsolation,
            yAxisIsolation, xAxisIsolation, motionReset, disabledState,
            horizontalResetEventCount: afterHorizontalReset.motionEventCount,
          };
        }""")
        assert result["firstTurn"]["quaternionChanged"]
        assert result["firstTurn"]["eventCount"] == 1
        assert result["firstTurn"]["event"] == {
            "reason": "rotate-y", "includesMesh": True}
        assert result["firstTurn"]["rootDelta"] == pytest.approx(math.pi / 2)
        assert result["firstTurn"]["projected"] == pytest.approx(math.pi / 2)
        assert result["firstTurn"]["angle"] < 0
        assert result["firstTurn"]["mode"] == "physics"
        assert not result["firstTurn"]["settled"]
        assert result["firstTurn"]["scheduled"]
        assert result["firstTurn"]["geometryChanged"]
        assert result["firstTurn"]["previewCalls"] == 0
        assert result["repeatedTurn"] == {
            "eventCount": 2, "moreLag": True, "scheduled": True}
        assert result["settledAfterTurns"]["settled"]
        assert not result["settledAfterTurns"]["scheduled"]
        assert result["settledAfterTurns"]["angles"] == pytest.approx([0, 0], abs=.002)
        assert result["settledAfterTurns"]["velocities"] == pytest.approx([0, 0], abs=.002)
        assert result["resetViewMotion"]["eventCount"] == 3
        assert result["resetViewMotion"]["positiveLag"]
        assert result["resetViewMotion"]["reason"] == "reset-view"
        assert result["resetViewMotion"]["scheduled"]
        assert result["resetViewMotion"]["referenceMatches"]
        assert result["settledAfterReset"]["settled"]
        assert not result["settledAfterReset"]["scheduled"]
        assert result["settledAfterReset"]["angles"] == pytest.approx([0, 0], abs=.002)
        assert result["cameraIsolation"]["cameraChanged"]
        assert result["cameraIsolation"]["eventCount"] == 3
        assert result["cameraIsolation"]["angle"] == pytest.approx(0, abs=.002)
        assert result["cameraIsolation"]["settled"]
        assert not result["cameraIsolation"]["scheduled"]
        assert result["yAxisIsolation"]["eventCount"] == 4
        assert result["yAxisIsolation"]["angle"] == pytest.approx(0, abs=.002)
        assert not result["yAxisIsolation"]["scheduled"]
        assert result["xAxisIsolation"]["eventCount"] == 6
        assert result["xAxisIsolation"]["angle"] < 0
        assert result["xAxisIsolation"]["scheduled"]
        assert result["xAxisIsolation"]["referenceMatches"]
        assert result["motionReset"] == {
            "angle": 0, "velocity": 0, "eventCount": 0,
            "scheduled": False, "referenceMatches": True}
        assert result["disabledState"] == {
            "quaternionUnchanged": True, "referenceCleared": True,
            "scheduled": False}
    finally:
        context.close()


def test_skinning_translation_motion_uses_scene_transform_lifecycle(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"TranslationLifecycle": _payload("TranslationLifecycle")})
    try:
        _open(page, "TranslationLifecycle")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        result = page.evaluate("""async () => {
          const THREE = await import('three');
          const scene = await import('./js/scene/scene.js');
          const experiment = await import('./js/mesh/weight-experiment.js');
          const mesh = window.modViewer.activeMeshes[0];
          const bytes = new Uint8Array(48);
          new Uint32Array(bytes.buffer).set([0, 1, 1, 2, 0, 2]);
          new Float32Array(bytes.buffer, 24).set([.8, .2, .7, .3, .6, .4]);
          const url = URL.createObjectURL(new Blob([bytes]));
          let previewCalls = 0;
          window.pywebview.api.get_skinning_preview = async () => {
            previewCalls += 1;
            return {
              status: 'ok', vertex_count: 3, influence_count: 2,
              bone_ids: [0, 1, 2], encoding: 'test',
              data: {
                url, length: 48,
                indices: {offset: 0, length: 24, type: 'u32'},
                weights: {offset: 24, length: 24, type: 'f32'},
              }, diagnostics: {},
            };
          };
          await experiment.loadSkinningWeights(mesh);
          experiment.ensureCandidateForest(mesh);
          previewCalls = 0;
          experiment.setPhysicsAxis(mesh, 'Z');
          experiment.setPhysicsLinearMotionStrength(mesh, .35);
          experiment.setPhysicsTargetAngle(mesh, 0);

          const queuedFrames = [];
          const originalRequestAnimationFrame = window.requestAnimationFrame;
          const originalCancelAnimationFrame = window.cancelAnimationFrame;
          window.requestAnimationFrame = callback => {
            queuedFrames.push(callback);
            return callback;
          };
          window.cancelAnimationFrame = callback => {
            const index = queuedFrames.indexOf(callback);
            if (index >= 0) queuedFrames.splice(index, 1);
          };
          const runFrame = timestamp => {
            const callbacks = queuedFrames.splice(0);
            if (!callbacks.length) throw new Error('Expected a queued frame.');
            callbacks.forEach(callback => callback(timestamp));
          };
          const settle = timestamp => {
            let current = timestamp;
            let frames = 0;
            while (queuedFrames.length && frames < 600) {
              runFrame(current);
              current += 16.7;
              frames += 1;
            }
            return {current, frames};
          };

          experiment.setPhysicsEnabled(mesh, true);
          runFrame(0);
          settle(16.7);
          const initialPositions = mesh.position.clone();
          const initialQuaternion = mesh.quaternion.clone();
          const initialGeometry = [...mesh.geometry.attributes.position.array];
          const radius = mesh.geometry.boundingSphere.radius;
          const delta = new THREE.Vector3(radius * .1, 0, 0);
          const events = [];
          window.addEventListener('mod-viewer-model-transform-changed', event => {
            if (event.detail?.meshes?.includes(mesh)) {
              events.push({
                reason: event.detail.reason,
                translation: event.detail.translationDeltaWorld,
              });
            }
          });
          const cameraBefore = scene.camera.position.clone();
          scene.translateModel(window.modViewer.activeMeshes, delta);
          const afterTranslation = experiment.getSkinningState(mesh);
          const firstPositionDelta = mesh.position.clone().sub(initialPositions);
          const firstTranslation = {
            positions: firstPositionDelta.toArray(),
            quaternionUnchanged: mesh.quaternion.equals(initialQuaternion),
            geometryChanged: mesh.geometry.attributes.position.array.some(
              (value, index) => Math.abs(value - initialGeometry[index]) > 1e-6),
            eventCount: afterTranslation.translationEventCount,
            world: afterTranslation.lastRootTranslationDeltaWorld,
            local: afterTranslation.lastRootTranslationDeltaLocal,
            lag: afterTranslation.lastTranslationLag,
            scheduled: experiment.isPhysicsScheduled(mesh),
            previewCalls,
            cameraUnchanged: scene.camera.position.equals(cameraBefore),
          };

          scene.translateModel(window.modViewer.activeMeshes, delta);
          const afterRepeat = experiment.getSkinningState(mesh);
          const repeated = {
            eventCount: afterRepeat.translationEventCount,
            positionDelta: mesh.position.clone().sub(initialPositions).toArray(),
            world: afterRepeat.lastRootTranslationDeltaWorld,
          };

          scene.rotateModelHorizontalQuarterTurn(window.modViewer.activeMeshes);
          const afterRotation = experiment.getSkinningState(mesh);
          const rotationOnly = {
            reason: events.at(-1)?.reason,
            translationCount: afterRotation.translationEventCount,
            world: afterRotation.lastRootTranslationDeltaWorld,
            motionCount: afterRotation.motionEventCount,
          };

          scene.resetView();
          const afterReset = experiment.getSkinningState(mesh);
          const resetEvent = events.at(-1);
          const resetState = {
            reason: resetEvent?.reason,
            reverse: resetEvent?.translation,
            translationCount: afterReset.translationEventCount,
            position: mesh.position.clone().toArray(),
            referenceMatches: afterReset.physicsReferenceQuaternion
              .equals(mesh.quaternion),
          };
          settle(33.4);
          const settled = experiment.getSkinningState(mesh);
          window.requestAnimationFrame = originalRequestAnimationFrame;
          window.cancelAnimationFrame = originalCancelAnimationFrame;
          URL.revokeObjectURL(url);
          return {firstTranslation, repeated, rotationOnly, resetState,
            settled: settled.physicsSettled,
            eventCount: events.length,
            initialPositions: initialPositions.toArray(),
          };
        }""")
        assert result["firstTranslation"]["positions"] == pytest.approx([
            result["firstTranslation"]["positions"][0], 0, 0])
        assert result["firstTranslation"]["quaternionUnchanged"]
        assert result["firstTranslation"]["geometryChanged"]
        assert result["firstTranslation"]["eventCount"] == 1
        assert result["firstTranslation"]["world"][0] > 0
        assert result["firstTranslation"]["local"][0] > 0
        assert abs(result["firstTranslation"]["lag"]) > 1e-6
        assert result["firstTranslation"]["scheduled"]
        assert result["firstTranslation"]["previewCalls"] == 0
        assert result["firstTranslation"]["cameraUnchanged"]
        assert result["repeated"]["eventCount"] == 2
        assert result["repeated"]["positionDelta"] == pytest.approx([
            result["firstTranslation"]["positions"][0] * 2, 0, 0])
        assert result["repeated"]["world"] == pytest.approx(
            result["firstTranslation"]["world"])
        assert result["rotationOnly"] == {
            "reason": "rotate-x", "translationCount": 2,
            "world": result["firstTranslation"]["world"], "motionCount": 3}
        assert result["resetState"]["reason"] == "reset-view"
        assert result["resetState"]["reverse"] == pytest.approx([
            -result["firstTranslation"]["world"][0] * 2, 0, 0])
        assert result["resetState"]["translationCount"] == 3
        assert result["resetState"]["position"] == pytest.approx(
            result["initialPositions"])
        assert result["resetState"]["referenceMatches"]
        assert result["settled"]
        assert result["eventCount"] == 4
    finally:
        context.close()


def test_skinning_gravity_lifecycle_refreshes_and_composes_with_motion(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"GravityLifecycle": _payload("GravityLifecycle")})
    try:
        _open(page, "GravityLifecycle")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        result = page.evaluate("""async () => {
          const mesh = window.modViewer.activeMeshes[0];
          const bytes = new Uint8Array(48);
          new Uint32Array(bytes.buffer).set([0, 1, 1, 2, 0, 2]);
          new Float32Array(bytes.buffer, 24).set([.8, .2, .7, .3, .6, .4]);
          const url = URL.createObjectURL(new Blob([bytes]));
          window.pywebview.api.get_skinning_preview = async () => ({
            status: 'ok', vertex_count: 3, influence_count: 2,
            bone_ids: [0, 1, 2], encoding: 'test',
            data: {
              url, length: 48,
              indices: {offset: 0, length: 24, type: 'u32'},
              weights: {offset: 24, length: 24, type: 'f32'},
            }, diagnostics: {},
          });
          const experiment = await import('./js/mesh/weight-experiment.js');
          await experiment.loadSkinningWeights(mesh);
          experiment.ensureCandidateForest(mesh);
          const state = experiment.getSkinningState(mesh);
          state.centerByBoneId = new Map([
            [0, [0, 0, 0]], [1, [1, 0, 0]], [2, [2, 0, 0]],
          ]);
          state.influenceGraph.boundingSphereRadius = 2;

          const queuedFrames = [];
          const originalRequestAnimationFrame = window.requestAnimationFrame;
          const originalCancelAnimationFrame = window.cancelAnimationFrame;
          window.requestAnimationFrame = callback => {
            queuedFrames.push(callback);
            return callback;
          };
          window.cancelAnimationFrame = callback => {
            const index = queuedFrames.indexOf(callback);
            if (index >= 0) queuedFrames.splice(index, 1);
          };
          const runFrame = timestamp => {
            const callbacks = queuedFrames.splice(0);
            if (!callbacks.length) throw new Error('Expected a queued frame.');
            callbacks.forEach(callback => callback(timestamp));
          };
          const settle = timestamp => {
            let current = timestamp;
            let frames = 0;
            while (queuedFrames.length && frames < 900) {
              runFrame(current);
              current += 16.7;
              frames += 1;
            }
            return {current, frames};
          };

          experiment.setPhysicsAxis(mesh, 'Z');
          experiment.setPhysicsEnabled(mesh, true);
          let timing = settle(0);
          experiment.setPhysicsGravityEnabled(mesh, true);
          const gravityEnabled = experiment.getSkinningState(mesh);
          const enabledSnapshot = {
            enabled: gravityEnabled.physicsGravityEnabled,
            map: [...gravityEnabled.physicsGravityAccelerations.entries()],
            local: gravityEnabled.physicsGravityLocal,
            scheduled: experiment.isPhysicsScheduled(mesh),
          };
          timing = settle(timing.current);
          const gravitySettled = experiment.getSkinningState(mesh);
          const initialGravity = {
            angle: gravitySettled.physicsState.joints.get(1).angle,
            settled: gravitySettled.physicsSettled,
            scheduled: experiment.isPhysicsScheduled(mesh),
            maxAcceleration: gravitySettled.physicsGravityDiagnostics
              .maxAbsTotalAcceleration,
          };

          const angleBeforeScale = gravitySettled.physicsState.joints.get(1).angle;
          experiment.setPhysicsGravityScale(mesh, .5);
          const scaledImmediate = experiment.getSkinningState(mesh);
          const scaleSnapshot = {
            scale: scaledImmediate.physicsGravityScale,
            anglePreserved: scaledImmediate.physicsState.joints.get(1).angle,
            scheduled: experiment.isPhysicsScheduled(mesh),
          };
          timing = settle(timing.current);
          const scaledSettled = experiment.getSkinningState(mesh);
          const scaleResult = {
            angleBefore: angleBeforeScale,
            angleAfter: scaledSettled.physicsState.joints.get(1).angle,
            settled: scaledSettled.physicsSettled,
          };

          const angleBeforeDisable = scaledSettled.physicsState.joints.get(1).angle;
          experiment.setPhysicsGravityEnabled(mesh, false);
          const gravityDisabled = experiment.getSkinningState(mesh);
          const disabledImmediate = {
            mapCleared: gravityDisabled.physicsGravityAccelerations === null,
            diagnosticsCleared: gravityDisabled.physicsGravityDiagnostics === null,
            angle: gravityDisabled.physicsState.joints.get(1).angle,
            scheduled: experiment.isPhysicsScheduled(mesh),
          };
          timing = settle(timing.current);
          const disabledSettled = experiment.getSkinningState(mesh);
          const disableResult = {
            angleBefore: angleBeforeDisable,
            angleAfter: disabledSettled.physicsState.joints.get(1).angle,
            settled: disabledSettled.physicsSettled,
          };

          experiment.setPhysicsGravityScale(mesh, 1);
          experiment.setPhysicsGravityEnabled(mesh, true);
          timing = settle(timing.current);
          const beforeTilt = experiment.getSkinningState(mesh);
          const beforeTiltLocal = beforeTilt.physicsGravityLocal;
          document.querySelector('#camera-flip-horizontal-btn').click();
          const afterTilt = experiment.getSkinningState(mesh);
          const tiltSnapshot = {
            localChanged: afterTilt.physicsGravityLocal.some((value, index) =>
              Math.abs(value - beforeTiltLocal[index]) > 1e-5),
            scheduled: experiment.isPhysicsScheduled(mesh),
            referenceMatches: afterTilt.physicsReferenceQuaternion
              .equals(mesh.quaternion),
          };
          experiment.setPhysicsAxis(mesh, 'Y');
          timing = settle(timing.current);
          const rotatedSettled = experiment.getSkinningState(mesh);
          const axisSnapshot = {
            axis: rotatedSettled.physicsAxis,
            local: rotatedSettled.physicsGravityLocal,
            angle: rotatedSettled.physicsState.joints.get(1).angle,
            settled: rotatedSettled.physicsSettled,
            maxAcceleration: rotatedSettled.physicsGravityDiagnostics
              .maxAbsTotalAcceleration,
          };

          const gravityOnly = experiment.getSkinningState(mesh);
          const gravitySnapshot = {
            gravityEnabled: gravityOnly.physicsGravityEnabled,
            gravityMapPresent: gravityOnly.physicsGravityAccelerations instanceof Map,
            physicsScheduled: experiment.isPhysicsScheduled(mesh),
          };

          experiment.resetPhysicsMotion(mesh);
          const resetImmediate = experiment.getSkinningState(mesh);
          const resetSnapshot = {
            enabled: resetImmediate.physicsGravityEnabled,
            angle: resetImmediate.physicsState.joints.get(1).angle,
            scheduled: experiment.isPhysicsScheduled(mesh),
          };
          timing = settle(timing.current);
          const resetSettled = experiment.getSkinningState(mesh);
          const resetResult = {
            angle: resetSettled.physicsState.joints.get(1).angle,
            settled: resetSettled.physicsSettled,
          };

          experiment.setPhysicsEnabled(mesh, false);
          const disabledPhysics = experiment.getSkinningState(mesh);
          window.requestAnimationFrame = originalRequestAnimationFrame;
          window.cancelAnimationFrame = originalCancelAnimationFrame;
          URL.revokeObjectURL(url);
          return {
            enabledSnapshot, initialGravity, scaleSnapshot, scaleResult,
            disabledImmediate, disableResult, tiltSnapshot, axisSnapshot,
            gravitySnapshot, resetSnapshot, resetResult,
            disabledPhysics: {
              enabled: disabledPhysics.physicsEnabled,
              gravityEnabled: disabledPhysics.physicsGravityEnabled,
              gravityMap: disabledPhysics.physicsGravityAccelerations,
              scheduled: experiment.isPhysicsScheduled(mesh),
            },
          };
        }""")
        assert result["enabledSnapshot"]["enabled"]
        assert result["enabledSnapshot"]["map"]
        assert result["enabledSnapshot"]["local"] == pytest.approx([0, -1, 0])
        assert result["enabledSnapshot"]["scheduled"]
        assert result["initialGravity"]["angle"] < 0
        assert result["initialGravity"]["settled"]
        assert not result["initialGravity"]["scheduled"]
        assert result["initialGravity"]["maxAcceleration"] > 0
        assert result["scaleSnapshot"]["scale"] == pytest.approx(.5)
        assert result["scaleSnapshot"]["anglePreserved"] == pytest.approx(
            result["scaleResult"]["angleBefore"])
        assert result["scaleSnapshot"]["scheduled"]
        assert result["scaleResult"]["angleAfter"] == pytest.approx(
            result["scaleResult"]["angleBefore"] / 2, abs=.01)
        assert result["scaleResult"]["settled"]
        assert result["disabledImmediate"]["mapCleared"]
        assert result["disabledImmediate"]["diagnosticsCleared"]
        assert result["disabledImmediate"]["angle"] == pytest.approx(
            result["disableResult"]["angleBefore"])
        assert result["disabledImmediate"]["scheduled"]
        assert result["disableResult"]["angleAfter"] == pytest.approx(0, abs=.002)
        assert result["disableResult"]["settled"]
        assert result["tiltSnapshot"]["localChanged"]
        assert result["tiltSnapshot"]["scheduled"]
        assert result["tiltSnapshot"]["referenceMatches"]
        assert result["axisSnapshot"]["axis"] == "Y"
        assert result["axisSnapshot"]["maxAcceleration"] > 0
        assert result["axisSnapshot"]["angle"] < 0
        assert result["axisSnapshot"]["settled"]
        assert result["gravitySnapshot"]["gravityEnabled"]
        assert result["gravitySnapshot"]["gravityMapPresent"]
        assert isinstance(result["gravitySnapshot"]["physicsScheduled"], bool)
        assert result["resetSnapshot"] == {
            "enabled": True, "angle": 0, "scheduled": True}
        assert result["resetResult"]["angle"] < 0
        assert result["resetResult"]["settled"]
        assert result["disabledPhysics"] == {
            "enabled": False, "gravityEnabled": False,
            "gravityMap": None, "scheduled": False}
    finally:
        context.close()




def test_skinning_joint_limits_lifecycle_preserves_state_and_cleanup(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url,
        {"JointLimitLifecycle": _payload("JointLimitLifecycle")})
    try:
        _open(page, "JointLimitLifecycle")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        result = page.evaluate("""async () => {
          const mesh = window.modViewer.activeMeshes[0];
          const bytes = new Uint8Array(48);
          new Uint32Array(bytes.buffer).set([0, 1, 1, 2, 0, 2]);
          new Float32Array(bytes.buffer, 24).set([.8, .2, .7, .3, .6, .4]);
          const url = URL.createObjectURL(new Blob([bytes]));
          window.pywebview.api.get_skinning_preview = async () => ({
            status: 'ok', vertex_count: 3, influence_count: 2,
            bone_ids: [0, 1, 2], encoding: 'test',
            data: {
              url, length: 48,
              indices: {offset: 0, length: 24, type: 'u32'},
              weights: {offset: 24, length: 24, type: 'f32'},
            }, diagnostics: {},
          });
          const experiment = await import('./js/mesh/weight-experiment.js');
          await experiment.loadSkinningWeights(mesh);
          experiment.ensureCandidateForest(mesh);
          const state = experiment.getSkinningState(mesh);
          state.centerByBoneId = new Map([
            [0, [0, 0, 0]], [1, [1, 0, 0]], [2, [2, 0, 0]],
          ]);
          state.influenceGraph.boundingSphereRadius = 2;

          const queuedFrames = [];
          const originalRequestAnimationFrame = window.requestAnimationFrame;
          const originalCancelAnimationFrame = window.cancelAnimationFrame;
          window.requestAnimationFrame = callback => {
            queuedFrames.push(callback);
            return callback;
          };
          window.cancelAnimationFrame = callback => {
            const index = queuedFrames.indexOf(callback);
            if (index >= 0) queuedFrames.splice(index, 1);
          };
          const runFrame = timestamp => {
            const callbacks = queuedFrames.splice(0);
            if (!callbacks.length) throw new Error('Expected a queued frame.');
            callbacks.forEach(callback => callback(timestamp));
          };
          const settle = timestamp => {
            let current = timestamp;
            let frames = 0;
            while (queuedFrames.length && frames < 900) {
              runFrame(current);
              current += 16.7;
              frames += 1;
            }
            return {current, frames};
          };
          const jointAngles = () => [...state.physicsState.joints.values()]
            .map(joint => joint.angle);
          const maxAngle = () => Math.max(...jointAngles().map(
            angle => Math.abs(angle)));
          const firstState = () => state.physicsState;

          experiment.setPhysicsEnabled(mesh, true);
          let timing = settle(0);
          experiment.setPhysicsConstraintsEnabled(mesh, true);
          experiment.setPhysicsMaxBendDegrees(mesh, 10);
          const enabledState = firstState();
          experiment.setPhysicsTargetAngle(mesh, 40);
          timing = settle(timing.current);
          const limited = {
            stateSame: state.physicsState === enabledState,
            limits: [...state.physicsJointLimits.values()],
            maxAngle: maxAngle(),
            settled: state.physicsSettled,
            scheduled: experiment.isPhysicsScheduled(mesh),
          };

          const limitedAngle = state.physicsState.joints.get(1).angle;
          experiment.setPhysicsMaxBendDegrees(mesh, 50);
          const loosenedImmediate = {
            stateSame: state.physicsState === enabledState,
            angle: state.physicsState.joints.get(1).angle,
            anglePreserved: state.physicsState.joints.get(1).angle
              === limitedAngle,
            limits: [...state.physicsJointLimits.values()],
            scheduled: experiment.isPhysicsScheduled(mesh),
          };
          timing = settle(timing.current);
          const loosened = {
            maxAngle: maxAngle(), settled: state.physicsSettled,
          };

          experiment.setPhysicsMaxBendDegrees(mesh, 10);
          const tightenedImmediate = {
            maxAngle: maxAngle(), scheduled: experiment.isPhysicsScheduled(mesh),
          };
          timing = settle(timing.current);
          const tightened = {
            maxAngle: maxAngle(), settled: state.physicsSettled,
          };

          experiment.setPhysicsTargetAngle(mesh, -40);
          timing = settle(timing.current);
          const reversed = {
            maxAngle: maxAngle(), angle: state.physicsState.joints.get(1).angle,
            settled: state.physicsSettled,
          };

          experiment.setPhysicsTargetAngle(mesh, 0);
          experiment.setPhysicsGravityEnabled(mesh, true);
          timing = settle(timing.current);
          const gravityLimit = {
            enabled: state.physicsGravityEnabled,
            angle: state.physicsState.joints.get(1).angle,
            maxAngle: maxAngle(), settled: state.physicsSettled,
            mapPresent: state.physicsGravityAccelerations instanceof Map,
          };

          experiment.setPhysicsGravityEnabled(mesh, false);
          timing = settle(timing.current);
          const gravityOff = {
            angle: state.physicsState.joints.get(1).angle,
            settled: state.physicsSettled,
            gravityMap: state.physicsGravityAccelerations,
          };

          experiment.resetPhysicsMotion(mesh);
          const reset = {
            angles: jointAngles(),
            constraintsEnabled: state.physicsConstraintsEnabled,
            mapPresent: state.physicsJointLimits instanceof Map,
            scheduled: experiment.isPhysicsScheduled(mesh),
          };

          experiment.setPhysicsConstraintsEnabled(mesh, false);
          const constraintsOff = {
            enabled: state.physicsConstraintsEnabled,
            map: state.physicsJointLimits,
            scheduled: experiment.isPhysicsScheduled(mesh),
          };
          timing = settle(timing.current);
          const constraintsOffSettled = {
            angles: jointAngles(), settled: state.physicsSettled,
          };

          experiment.setPhysicsEnabled(mesh, false);
          const disabled = {
            physicsEnabled: state.physicsEnabled,
            physicsState: state.physicsState,
            constraintsEnabled: state.physicsConstraintsEnabled,
            limits: state.physicsJointLimits,
            scheduled: experiment.isPhysicsScheduled(mesh),
          };
          window.requestAnimationFrame = originalRequestAnimationFrame;
          window.cancelAnimationFrame = originalCancelAnimationFrame;
          URL.revokeObjectURL(url);
          return {
            limited, loosenedImmediate, loosened, tightenedImmediate, tightened,
            reversed, gravityLimit, gravityOff, reset, constraintsOff,
            constraintsOffSettled, disabled,
          };
        }""")
        assert result["limited"]["stateSame"]
        assert result["limited"]["limits"] == pytest.approx([
            math.radians(5), math.radians(5)])
        assert result["limited"]["maxAngle"] <= math.radians(5) + 1e-6
        assert result["limited"]["settled"]
        assert not result["limited"]["scheduled"]
        assert result["loosenedImmediate"]["stateSame"]
        assert result["loosenedImmediate"]["anglePreserved"]
        assert result["loosenedImmediate"]["limits"] == pytest.approx([
            math.radians(25), math.radians(25)])
        assert result["loosenedImmediate"]["scheduled"]
        assert result["loosened"]["maxAngle"] == pytest.approx(
            math.radians(20), abs=1e-3)
        assert result["loosened"]["settled"]
        assert result["tightenedImmediate"]["maxAngle"] <= math.radians(5) + 1e-6
        assert result["tightenedImmediate"]["scheduled"]
        assert result["tightened"]["maxAngle"] <= math.radians(5) + 1e-6
        assert result["tightened"]["settled"]
        assert result["reversed"]["angle"] == pytest.approx(
            math.radians(-5), abs=1e-3)
        assert result["reversed"]["maxAngle"] <= math.radians(5) + 1e-6
        assert result["reversed"]["settled"]
        assert result["gravityLimit"]["enabled"]
        assert result["gravityLimit"]["angle"] < 0
        assert result["gravityLimit"]["maxAngle"] <= math.radians(5) + 1e-6
        assert result["gravityLimit"]["settled"]
        assert result["gravityLimit"]["mapPresent"]
        assert result["gravityOff"]["angle"] == pytest.approx(0, abs=1e-3)
        assert result["gravityOff"]["settled"]
        assert result["gravityOff"]["gravityMap"] is None
        assert result["reset"]["angles"] == pytest.approx([0, 0])
        assert result["reset"]["constraintsEnabled"]
        assert result["reset"]["mapPresent"]
        assert not result["reset"]["scheduled"]
        assert not result["constraintsOff"]["enabled"]
        assert result["constraintsOff"]["map"] is None
        assert result["constraintsOff"]["scheduled"]
        assert result["constraintsOffSettled"]["angles"] == pytest.approx([0, 0])
        assert result["constraintsOffSettled"]["settled"]
        assert not result["disabled"]["physicsEnabled"]
        assert result["disabled"]["physicsState"] is None
        assert not result["disabled"]["constraintsEnabled"]
        assert result["disabled"]["limits"] is None
        assert not result["disabled"]["scheduled"]
    finally:
        context.close()




def test_skinning_inspector_keeps_normal_controls_and_builds_hierarchy_lazily(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"SkinningInspector": _payload("SkinningInspector")})
    try:
        _open(page, "SkinningInspector")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        page.evaluate("""async () => {
          const bytes = new Uint8Array(48);
          new Uint32Array(bytes.buffer).set([0, 1, 1, 2, 0, 2]);
          new Float32Array(bytes.buffer, 24).set([.8, .2, .7, .3, .6, .4]);
          const url = URL.createObjectURL(new Blob([bytes]));
          window.pywebview.api.get_skinning_preview = async () => ({
            status: 'ok', vertex_count: 3, influence_count: 2,
            bone_ids: [0, 1, 2], encoding: 'test',
            data: {
              url, length: 48,
              indices: {offset: 0, length: 24, type: 'u32'},
              weights: {offset: 24, length: 24, type: 'f32'},
            }, diagnostics: {},
          });
          Object.defineProperty(navigator, 'clipboard', {
            configurable: true,
            value: {
              writeText: async value => {
                window.__copiedSkinningDiagnostics = value;
              },
            },
          });
        }""")
        page.locator(".draw-item").click()
        page.locator(".inspector-skinning-load").wait_for()
        page.locator(".inspector-skinning-load").click()
        page.wait_for_function("""() =>
          document.querySelector('.inspector-skinning-load')?.textContent
            === 'Weights loaded'
        """)

        loaded = page.evaluate("""async () => {
          const {getSkinningState} =
            await import('./js/mesh/weight-experiment.js');
          const state = getSkinningState(window.modViewer.activeMeshes[0]);
          return {
            loaded: state.loaded,
            nodes: state.influenceNodes?.length || 0,
            graph: state.influenceGraph,
            forest: state.candidateForest,
            title: document.querySelector('.inspector-skinning-title')?.textContent,
            stats: document.querySelector('.inspector-skinning-influence-stats')
              ?.textContent || '',
            oldControls: [...document.querySelectorAll(
              '.inspector-skinning-chain, .inspector-skinning-translation, '
                + '.inspector-skinning-kick')].length,
          };
        }""")
        assert loaded["loaded"]
        assert loaded["nodes"] == 3
        assert loaded["graph"] is None
        assert loaded["forest"] is None
        assert loaded["title"] == "Skin Weights"
        assert "Affected vertices" in loaded["stats"]
        assert loaded["oldControls"] == 0

        page.locator(".inspector-skinning-center").click()
        centered = page.evaluate("""async () => {
          const {getSkinningState} =
            await import('./js/mesh/weight-experiment.js');
          const state = getSkinningState(window.modViewer.activeMeshes[0]);
          return {
            mode: state.influenceVisualizationMode,
            graph: state.influenceGraph,
            forest: state.candidateForest,
          };
        }""")
        assert centered == {"mode": "center", "graph": None, "forest": None}

        page.locator(".inspector-skinning-physics-enable").check()
        physics_built = page.evaluate("""async () => {
          const {getSkinningState} =
            await import('./js/mesh/weight-experiment.js');
          const state = getSkinningState(window.modViewer.activeMeshes[0]);
          return {
            enabled: state.physicsEnabled,
            graph: !!state.influenceGraph,
            forest: !!state.candidateForest,
            mode: state.deformationMode,
          };
        }""")
        assert physics_built == {
            "enabled": True, "graph": True, "forest": True, "mode": "physics"}

        page.locator(".inspector-skinning-hierarchy-show").click()
        page.wait_for_function("""() =>
          document.querySelector('.inspector-skinning-hierarchy-show')
            ?.textContent === 'Hide Hierarchy'
        """)
        page.evaluate("""async () => {
          const {getSkinningState} =
            await import('./js/mesh/weight-experiment.js');
          window.__skinGraphBeforeRoot =
            getSkinningState(window.modViewer.activeMeshes[0]).influenceGraph;
        }""")
        page.locator(".inspector-skinning-hierarchy-root").select_option("1")
        page.wait_for_function("""() =>
          document.querySelector('.inspector-skinning-hierarchy-root')?.value === '1'
        """)
        hierarchy = page.evaluate("""async () => {
          const {getSkinningState} =
            await import('./js/mesh/weight-experiment.js');
          const state = getSkinningState(window.modViewer.activeMeshes[0]);
          return {
            root: state.candidateTree?.rootId,
            graphReused: state.influenceGraph === window.__skinGraphBeforeRoot,
            summary: document.querySelector(
              '.inspector-skinning-hierarchy-summary')?.textContent || '',
            output: document.querySelector(
              '.inspector-skinning-hierarchy-output')?.textContent || '',
          };
        }""")
        assert hierarchy["root"] == 1
        assert hierarchy["graphReused"]
        assert "Inferred influence hierarchy" in hierarchy["summary"]
        assert "Root 1" in hierarchy["output"]

        page.locator(".inspector-skinning-copy-skinning").click()
        page.wait_for_function(
            "() => document.querySelector('.inspector-skinning-copy-status')"
            "?.textContent.includes('copied')")
        copied = page.evaluate("""() => JSON.parse(
          window.__copiedSkinningDiagnostics || '{}')""")
        assert copied["skinning"]["boneIds"] == [0, 1, 2]
        assert copied["hierarchy"]["rootId"] == 1
        assert copied["physics"]["enabled"]

        labels = page.locator(".inspector-skinning-group").inner_text()
        order = page.locator(
            ".inspector-skinning-advanced > section").evaluate_all(
                "nodes => nodes.map(node => ({className: node.className, "
                "tagName: node.tagName}))")
        assert order == [
            {"className": "inspector-section inspector-skinning-physics",
             "tagName": "SECTION"},
            {"className": "inspector-section inspector-skinning-hierarchy",
             "tagName": "SECTION"},
        ]
        assert "Inferred Influence Hierarchy" in labels
        assert "Secondary Motion" in labels
        assert "Kick" not in labels
        assert "Move Axis" not in labels
        assert "Build Candidate Tree" not in labels
        page.locator(".inspector-skinning-physics-enable").uncheck()
    finally:
        context.close()


def test_viewport_pipeline_uses_character_layers_and_stable_ao_settings(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"Pipeline": _payload("Pipeline")})
    errors = []
    page.on("pageerror", lambda error: errors.append(error))
    try:
        _open(page, "Pipeline")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        _set_ao_level(page, 100)
        page.wait_for_function("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          const state = getViewportRenderPipelineDebugState();
          return state.strength === 1 && !state.pipelineNeedsUpdate;
        }""")
        state = page.evaluate("""async () => {
          const THREE = await import('three');
          const {getViewportRenderPipelineDebugState, scene} =
            await import('./js/scene/scene.js');
          const {CHARACTER_AO_LAYER} =
            await import('./js/scene/viewer-layers.js');
          const mesh = window.modViewer.activeMeshes[0];
          const layer = new THREE.Layers();
          layer.set(CHARACTER_AO_LAYER);
          const helpers = [];
          scene.traverse(object => {
            if (object.userData.isViewerGround || object.isGridHelper
                || object.isSprite || object.userData.isViewerOutline) {
              helpers.push({
                ground: object.userData.isViewerGround === true,
                grid: object.isGridHelper === true,
                sprite: object.isSprite === true,
                outline: object.userData.isViewerOutline === true,
                hasCharacterLayer: object.layers.test(layer),
              });
            }
          });
          return {
            pipeline: getViewportRenderPipelineDebugState(),
            meshHasCharacterLayer: mesh.layers.test(layer),
            helpers,
            viewerRenderCount: window.modViewer.getRenderCount(),
          };
        }""")
        assert state["pipeline"]["hasRenderPipeline"]
        assert state["pipeline"]["hasPrePass"]
        assert state["pipeline"]["hasGTAO"]
        assert state["pipeline"]["prePassLayerMask"] == 1 << 1
        assert state["pipeline"]["characterAOLayer"] == 1
        assert state["pipeline"]["samples"] == 8
        assert state["pipeline"]["prePassSamples"] == 1
        assert state["pipeline"]["prePassResolutionScale"] == pytest.approx(0.5)
        assert state["pipeline"]["beautyCameraIsSource"]
        assert state["pipeline"]["prePassCameraIsClone"]
        assert state["pipeline"]["cameraCoordinateSystem"] == (
            state["pipeline"]["rendererCoordinateSystem"])
        assert state["pipeline"]["resolutionScale"] == pytest.approx(0.5)
        assert state["pipeline"]["temporalFiltering"] is False
        assert state["pipeline"]["strength"] == pytest.approx(1)
        assert not state["pipeline"]["pipelineNeedsUpdate"]
        assert state["pipeline"]["renderCount"] == state["viewerRenderCount"]
        assert state["meshHasCharacterLayer"]
        assert all(not helper["hasCharacterLayer"] for helper in state["helpers"])
        assert not errors, "\n".join(str(error) for error in errors)
    finally:
        context.close()

def test_camera_zoom_is_cursor_centered(edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"CursorZoom": _payload("CursorZoom")})
    try:
        _open(page, "CursorZoom")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        state = page.evaluate("""async () => {
          const {camera, controls} = await import('./js/scene/scene.js');
          return {
            cursorZoom: controls.cursorZoom,
            target: controls.target.toArray(),
            cameraPosition: camera.position.toArray(),
            cameraUp: camera.up.toArray(),
            cameraQuaternion: camera.quaternion.toArray(),
          };
        }""")
        assert state["cursorZoom"] is True

        canvas = page.locator("canvas").bounding_box()
        render_count = page.evaluate("window.modViewer.getRenderCount()")
        page.mouse.move(
            canvas["x"] + canvas["width"] * 0.25,
            canvas["y"] + canvas["height"] * 0.35)
        page.mouse.wheel(0, 100)
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() > count",
            arg=render_count)
        zoomed = page.evaluate("""async () => {
          const {camera, controls} = await import('./js/scene/scene.js');
          return {
            target: controls.target.toArray(),
            cameraPosition: camera.position.toArray(),
          };
        }""")
        assert zoomed["target"] == pytest.approx(state["target"])
        movement = [zoomed["cameraPosition"][i] - state["cameraPosition"][i]
                    for i in range(3)]
        direction = [state["cameraPosition"][i] - state["target"][i]
                     for i in range(3)]
        cross = (
            movement[1] * direction[2] - movement[2] * direction[1],
            movement[2] * direction[0] - movement[0] * direction[2],
            movement[0] * direction[1] - movement[1] * direction[0],
        )
        assert math.sqrt(sum(value * value for value in movement)) > 0
        assert math.sqrt(sum(value * value for value in cross)) > 1e-5

        page.mouse.move(
            canvas["x"] + canvas["width"] * 0.25,
            canvas["y"] + canvas["height"] * 0.35)
        for _ in range(8):
            page.mouse.wheel(0, 100)
            page.wait_for_timeout(25)
        page.wait_for_timeout(200)
        zoomed_far = page.evaluate("""async () => {
          const {camera, controls} = await import('./js/scene/scene.js');
          return {
            target: controls.target.toArray(),
            cameraPosition: camera.position.toArray(),
          };
        }""")
        initial_distance = math.sqrt(sum(
            (state["cameraPosition"][i] - state["target"][i]) ** 2
            for i in range(3)))
        far_distance = math.sqrt(sum(
            (zoomed_far["cameraPosition"][i] - zoomed_far["target"][i]) ** 2
            for i in range(3)))
        assert far_distance > initial_distance

        before_reset = page.evaluate("window.modViewer.getRenderCount()")
        page.locator("#camera-reset-view-btn").click()
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() > count", arg=before_reset)
        reset = page.evaluate("""async () => {
          const {camera, controls} = await import('./js/scene/scene.js');
          return {
            target: controls.target.toArray(),
            cameraPosition: camera.position.toArray(),
            cameraUp: camera.up.toArray(),
            cameraQuaternion: camera.quaternion.toArray(),
          };
        }""")
        assert reset["target"] == pytest.approx(state["target"])
        assert reset["cameraPosition"] == pytest.approx(state["cameraPosition"])
        assert reset["cameraUp"] == pytest.approx(state["cameraUp"])
        assert reset["cameraQuaternion"] == pytest.approx(state["cameraQuaternion"])

        center = page.evaluate("""async () => {
          const {camera, controls} = await import('./js/scene/scene.js');
          const rect = document.querySelector('canvas').getBoundingClientRect();
          const projected = controls.target.clone().project(camera);
          return {
            x: rect.left + (projected.x + 1) * rect.width * 0.5,
            y: rect.top + (1 - projected.y) * rect.height * 0.5,
          };
        }""")
        page.mouse.move(center["x"], center["y"])
        before_center_zoom = page.evaluate("window.modViewer.getRenderCount()")
        page.mouse.wheel(0, 100)
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() > count",
            arg=before_center_zoom)
        centered = page.evaluate("""async () => {
          const {camera, controls} = await import('./js/scene/scene.js');
          return {
            target: controls.target.toArray(),
            cameraPosition: camera.position.toArray(),
          };
        }""")
        assert centered["target"] == pytest.approx(reset["target"])
        center_movement = [centered["cameraPosition"][i]
                           - reset["cameraPosition"][i] for i in range(3)]
        reset_direction = [reset["cameraPosition"][i] - reset["target"][i]
                           for i in range(3)]
        center_cross = (
            center_movement[1] * reset_direction[2]
              - center_movement[2] * reset_direction[1],
            center_movement[2] * reset_direction[0]
              - center_movement[0] * reset_direction[2],
            center_movement[0] * reset_direction[1]
              - center_movement[1] * reset_direction[0],
        )
        center_movement_length = math.sqrt(sum(
            value * value for value in center_movement))
        center_cross_length = math.sqrt(sum(
            value * value for value in center_cross))
        assert center_movement_length > 0
        assert center_cross_length / center_movement_length < 1e-3

        before_second_reset = page.evaluate("window.modViewer.getRenderCount()")
        page.locator("#camera-reset-view-btn").click()
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() > count",
            arg=before_second_reset)
        second_reset = page.evaluate("""async () => {
          const {camera, controls} = await import('./js/scene/scene.js');
          return {
            target: controls.target.toArray(),
            cameraPosition: camera.position.toArray(),
            cameraUp: camera.up.toArray(),
            cameraQuaternion: camera.quaternion.toArray(),
          };
        }""")
        assert second_reset["target"] == pytest.approx(reset["target"])
        assert second_reset["cameraPosition"] == pytest.approx(reset["cameraPosition"])
        assert second_reset["cameraUp"] == pytest.approx(reset["cameraUp"])
        assert second_reset["cameraQuaternion"] == pytest.approx(reset["cameraQuaternion"])
    finally:
        context.close()

def test_viewport_pipeline_uses_model_scale_and_wireframe_bypass(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"Pipeline": _payload("Pipeline")})
    try:
        _open(page, "Pipeline")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        _set_ao_level(page, 100)
        initial = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        assert initial["modelSize"] > 0
        assert initial["radius"] == pytest.approx(initial["modelSize"] * 0.005)
        assert initial["thickness"] == pytest.approx(initial["radius"] * 4)
        assert initial["effectiveStrength"] == pytest.approx(initial["strength"])
        assert initial["directRenderCount"] > 0
        assert initial["aoRenderCount"] > 0
        assert (initial["directRenderCount"] + initial["aoRenderCount"]
                == initial["renderCount"])

        before_wireframe = initial["renderCount"]
        page.locator("#wire-btn").click()
        suppressed = page.evaluate("""async stateBefore => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          const deadline = performance.now() + 5000;
          while (performance.now() < deadline) {
            const state = getViewportRenderPipelineDebugState();
            if (state.effectiveStrength === 0
                && state.renderCount > stateBefore.renderCount
                && state.directRenderCount > stateBefore.directRenderCount) {
              return state;
            }
            await new Promise(resolve => setTimeout(resolve, 16));
          }
          throw new Error('wireframe did not produce a direct render');
        }""", arg={
            "renderCount": before_wireframe,
            "directRenderCount": initial["directRenderCount"],
        })
        assert suppressed["enabled"]
        assert suppressed["strength"] == pytest.approx(initial["strength"])
        assert suppressed["effectiveStrength"] == 0
        assert suppressed["directRenderCount"] > initial["directRenderCount"]
        assert suppressed["aoRenderCount"] == initial["aoRenderCount"]
        assert not suppressed["pipelineNeedsUpdate"]

        before_restore = suppressed["renderCount"]
        page.locator("#wire-btn").click()
        page.wait_for_function("""async count => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          const state = getViewportRenderPipelineDebugState();
          return state.effectiveStrength > 0 && state.renderCount > count;
        }""", arg=before_restore)
        restored = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        assert restored["effectiveStrength"] == pytest.approx(initial["strength"])
        assert restored["modelSize"] == pytest.approx(initial["modelSize"])
        assert restored["radius"] == pytest.approx(initial["radius"])
        assert restored["aoRenderCount"] > suppressed["aoRenderCount"]
        assert restored["pipelineId"] == initial["pipelineId"]
    finally:
        context.close()

def test_viewport_ambient_occlusion_slider_selects_direct_or_gtao_path(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"AO": _payload("AO")})
    try:
        _open(page, "AO")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        startup = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        assert not startup["enabled"]
        assert startup["radiusFactor"] == pytest.approx(0.005)
        assert startup["radius"] == pytest.approx(0.001 * 0.005)
        assert startup["thickness"] == pytest.approx(startup["radius"] * 4)
        assert startup["directRenderCount"] > 0
        assert startup["aoRenderCount"] == 0
        pipeline_id = startup["pipelineId"]

        for level, strength in ((1, 0.01), (50, 0.5), (99, 0.99), (100, 1)):
            _set_ao_level(page, level)
            state = page.evaluate("""async () => {
              const {getViewportRenderPipelineDebugState} =
                await import('./js/scene/scene.js');
              return getViewportRenderPipelineDebugState();
            }""")
            assert state["enabled"]
            assert state["radiusFactor"] == pytest.approx(0.005)
            assert state["strength"] == pytest.approx(strength)
            assert state["effectiveStrength"] == pytest.approx(strength)
            assert state["radius"] == pytest.approx(state["modelSize"] * 0.005)
            assert state["thickness"] == pytest.approx(state["radius"] * 4)
            assert state["aoRenderCount"] > 0
            assert state["pipelineId"] == pipeline_id

        initial = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        assert initial["radiusFactor"] == pytest.approx(0.005)
        direct_before = initial["directRenderCount"]
        ao_before = initial["aoRenderCount"]
        _set_ao_level(page, 0)
        page.wait_for_function("""async count => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          const state = getViewportRenderPipelineDebugState();
          return state.radiusFactor === 0.005 && state.effectiveStrength === 0
            && state.directRenderCount > count;
        }""", arg=direct_before)
        disabled = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        assert not disabled["enabled"]
        assert disabled["radiusFactor"] == pytest.approx(0.005)
        assert disabled["radius"] == pytest.approx(0.001 * 0.005)
        assert disabled["thickness"] == pytest.approx(disabled["radius"] * 4)
        assert disabled["effectiveStrength"] == 0
        assert disabled["aoRenderCount"] == ao_before
        assert disabled["pipelineId"] == pipeline_id
    finally:
        context.close()

def test_viewport_bloom_selects_only_its_required_render_graph(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx")
    payload["meshes"]["Body-Packed-0"]["emission_map_key"] = (
        "emission_map::Packed-glow.png")
    payload["textures"]["emission_map::Packed-glow.png"] = _PNG_URI
    context, page = _page(edge_browser, frontend_url, {"Bloom": payload})
    try:
        _open(page, "Bloom")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        initial = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        bloom_button = page.locator("#bloom-btn")
        assert not bloom_button.is_hidden()
        assert not bloom_button.is_disabled()
        assert initial["activeRenderMode"] == "direct"
        assert not initial["bloomEnabled"]
        assert initial["bloomAvailable"]
        assert initial["hasBloom"]

        page.locator("#bloom-btn").click()
        page.wait_for_function("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          const state = getViewportRenderPipelineDebugState();
          return state.activeRenderMode === 'bloom' && state.bloomOnlyRenderCount > 0;
        }""")
        bloom_only = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        assert bloom_only["bloomEffective"]
        assert bloom_only["bloomAvailable"]
        assert bloom_only["aoOnlyRenderCount"] == 0
        assert page.locator("#bloom-btn").get_attribute("aria-pressed") == "true"

        _set_ao_level(page, 100)
        page.wait_for_function("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          const state = getViewportRenderPipelineDebugState();
          return state.activeRenderMode === 'ao-bloom' && state.aoBloomRenderCount > 0;
        }""")
        page.evaluate("window.modViewer.setBloomEnabled(false)")
        page.wait_for_function("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          const state = getViewportRenderPipelineDebugState();
          return state.activeRenderMode === 'ao' && state.aoOnlyRenderCount > 0;
        }""")
        _set_ao_level(page, 0)
        page.wait_for_function("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState().activeRenderMode === 'direct';
        }""")

        page.evaluate("window.modViewer.setBloomEnabled(true)")
        page.wait_for_function("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState().activeRenderMode === 'bloom';
        }""")
        page.locator("#wire-btn").click()
        suppressed = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        assert suppressed["activeRenderMode"] == "direct"
        assert suppressed["bloomEnabled"]
        assert suppressed["bloomSuppressedByWireframe"]
        page.locator("#wire-btn").click()
        page.evaluate("window.modViewer.setMaterialDebugMode('material-id')")
        debug_suppressed = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        assert debug_suppressed["activeRenderMode"] == "direct"
        assert debug_suppressed["bloomSuppressedByDebug"]
        page.evaluate("window.modViewer.setMaterialDebugMode('off')")
        page.wait_for_function("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState().activeRenderMode === 'bloom';
        }""")
    finally:
        context.close()


def test_bloom_control_is_hidden_without_supported_emission(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"Plain": _payload("Plain")})
    try:
        _open(page, "Plain")
        page.locator(".draw-item").wait_for()
        button = page.locator("#bloom-btn")
        assert button.is_hidden()
        assert button.is_disabled()
        state = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        assert not state["bloomAvailable"]
        assert not state["bloomEffective"]
        assert button.get_attribute("aria-label") == (
            "Emission bloom unavailable: no GlowMap detected")
    finally:
        context.close()


def test_bloom_availability_suppresses_pipeline_and_restores_preference(
        edge_browser, frontend_url):
    supported = _packed_material_payload("wuwa:rabbitfx")
    supported["meshes"]["Body-Packed-0"]["emission_map_key"] = (
        "emission_map::Packed-glow.png")
    supported["textures"]["emission_map::Packed-glow.png"] = _PNG_URI
    context, page = _page(edge_browser, frontend_url, {
        "Bloom": supported, "Plain": _payload("Plain"),
    })
    try:
        _open(page, "Bloom")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        page.evaluate("window.modViewer.setBloomEnabled(true)")
        page.wait_for_function("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          const state = getViewportRenderPipelineDebugState();
          return state.activeRenderMode === 'bloom'
            && state.bloomAvailable && state.bloomEffective;
        }""")
        _set_ao_level(page, 100)
        page.wait_for_function("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState().activeRenderMode
            === 'ao-bloom';
        }""")

        _open(page, "Plain")
        page.wait_for_function("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          const state = getViewportRenderPipelineDebugState();
          return window.modViewer.activeMeshes.length === 1
            && !state.bloomAvailable
            && !state.bloomEffective
            && state.activeRenderMode === 'ao'
            && state.aoOnlyRenderCount > 0;
        }""")
        unsupported = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        assert unsupported["bloomEnabled"]
        assert not unsupported["bloomAvailable"]
        assert not unsupported["bloomEffective"]
        assert page.locator("#bloom-btn").is_hidden()

        _open(page, "Bloom")
        page.wait_for_function("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          const state = getViewportRenderPipelineDebugState();
          return window.modViewer.activeMeshes.length === 1
            && state.bloomAvailable
            && state.bloomEffective
            && state.activeRenderMode === 'ao-bloom';
        }""")
        button = page.locator("#bloom-btn")
        assert not button.is_hidden()
        assert not button.is_disabled()
    finally:
        context.close()


def test_emission_variants_keep_bloom_available_without_active_map(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx")
    entry = payload["meshes"]["Body-Packed-0"]
    entry["emission_map_variants"] = [{
        "conditions": [[{"var": "menu", "value": "1", "negate": False}]],
        "tex_key": "emission_map::Packed-conditional-glow.png",
    }]
    payload["textures"]["emission_map::Packed-conditional-glow.png"] = _PNG_URI
    context, page = _page(edge_browser, frontend_url, {"Conditional": payload})
    try:
        _open(page, "Conditional")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        state = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          const mesh = window.modViewer.activeMeshes[0];
          const pipeline = getViewportRenderPipelineDebugState();
          return {
            variantCount: mesh.userData.emissionMapVariants.length,
            resolvedEmissionMapKey: mesh.userData.resolvedEmissionMapKey,
            bloomAvailable: pipeline.bloomAvailable,
          };
        }""")
        assert state == {
            "variantCount": 1,
            "resolvedEmissionMapKey": None,
            "bloomAvailable": True,
        }
        assert not page.locator("#bloom-btn").is_hidden()
        assert not page.locator("#bloom-btn").is_disabled()
    finally:
        context.close()


@pytest.mark.parametrize("scale", [0.01, 1, 100])
def test_viewport_gtao_radius_scales_with_model_bounds(edge_browser, frontend_url, scale):
    label = f"PipelineScale{scale}"
    payload = _payload(label)
    entry = payload["meshes"][f"Body-{label}-0"]
    entry["pos"] = _f32(0, 0, 0, scale, 0, 0, 0, scale, scale * 0.25)
    context, page = _page(edge_browser, frontend_url, {label: payload})
    try:
        _open(page, label)
        page.locator(".draw-item").wait_for()
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        _set_ao_level(page, 100)
        state = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        assert math.isfinite(state["modelSize"])
        assert math.isfinite(state["radius"])
        assert state["modelSize"] > 0
        assert state["radius"] / state["modelSize"] == pytest.approx(0.005)
        assert state["thickness"] == pytest.approx(state["radius"] * 4)
    finally:
        context.close()

def test_viewport_pipeline_resizes_pass_targets_without_rebuilding(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"Resize": _payload("Resize")})
    try:
        _open(page, "Resize")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        _set_ao_level(page, 100)
        initial = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        assert initial["resolution"][0] > 0
        assert initial["resolution"][1] > 0
        render_count = page.evaluate("window.modViewer.getRenderCount()")

        page.evaluate("""async () => {
          const {renderer} = await import('./js/scene/scene.js');
          const {requestRender} = await import('./js/scene/render-scheduler.js');
          renderer.setSize(900, 640);
          requestRender();
        }""")
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() > count", arg=render_count)
        resized = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        assert resized["resolution"][0] > 0
        assert resized["resolution"][1] > 0
        assert resized["resolutionScale"] == initial["resolutionScale"]
        assert not resized["pipelineNeedsUpdate"]
    finally:
        context.close()

def test_viewport_gtao_is_visible_and_does_not_add_continuous_frames(
        edge_browser, frontend_url):
    payload = _payload("AO")
    entry = payload["meshes"]["Body-AO-0"]
    entry["drawindexed"] = [12, 0, 0]
    entry["pos"] = _f32(
        -0.7, 0, -0.5, 0.7, 0, -0.5, 0.7, 0, 0.5, -0.7, 0, 0.5,
        -0.7, 0, -0.5, 0.7, 0, -0.5, 0.7, 0.9, -0.5, -0.7, 0.9, -0.5,
    )
    entry["idx"] = _u32(0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7)
    context, page = _page(edge_browser, frontend_url, {"AO": payload})
    try:
        _open(page, "AO")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        _set_ao_level(page, 100)
        page.locator("#ao-btn").click()
        page.wait_for_timeout(250)
        default_ao = Image.open(io.BytesIO(page.screenshot())).convert("RGB")

        _set_ao_level(page, 0)
        page.locator("#ao-btn").click()
        without_default_ao = Image.open(
            io.BytesIO(page.screenshot())).convert("RGB")
        assert ImageChops.difference(default_ao, without_default_ao).getbbox()

        without_render_count = page.evaluate("window.modViewer.getRenderCount()")
        page.wait_for_timeout(200)
        assert page.evaluate("window.modViewer.getRenderCount()") == without_render_count
    finally:
        context.close()

def test_direct_dds_matches_png_orientation_and_diffuse_color(
        edge_browser, frontend_url, tmp_path):
    dds_path = tmp_path / "orientation.dds"
    dds_path.write_bytes(_dxt1_vertical_gradient())
    publication = server.begin_texture_publication(str(tmp_path))
    dds_url = publication.register(str(dds_path))
    publication.commit()
    png_url = dds_url[:-4] + ".png"
    payload = _parity_payload(dds_url)
    context, page = _page(edge_browser, frontend_url, {"Parity": payload})
    try:
        _open(page, "Parity")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings?.diffuse?.textureNode?.value?.image?.width === 4")
        page.wait_for_timeout(250)
        direct_pixels = [
            _sample_mesh_pixel_at(page, 0, 0.65),
            _sample_mesh_pixel_at(page, 0, -0.65),
        ]

        page.evaluate("""async ({key, uri}) => {
          const {refreshMeshTexture, setTextures} = await import('./js/mesh/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          setTextures({[key]: uri});
          refreshMeshTexture(mesh);
        }""", {"key": "diffuse::Parity-one.png", "uri": png_url})
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings?.diffuse?.textureNode?.value?.image?.width === 4"
            " && window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings?.diffuse?.textureNode?.value?.isCompressedTexture !== true")
        page.wait_for_timeout(250)
        png_pixels = [
            _sample_mesh_pixel_at(page, 0, 0.65),
            _sample_mesh_pixel_at(page, 0, -0.65),
        ]

        assert all(
            max(abs(left - right) for left, right in zip(direct, fallback)) <= 40
            for direct, fallback in zip(direct_pixels, png_pixels)), (
                direct_pixels, png_pixels)
        assert direct_pixels[0] != direct_pixels[1]
        assert png_pixels[0] != png_pixels[1]
    finally:
        context.close()

def test_texture_stays_fallback_until_png_load_completes(
        edge_browser, frontend_url):
    payload = _payload("Delayed")
    entry = payload["meshes"]["Body-Delayed-0"]
    entry["pos"] = _f32(-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0)
    entry["idx"] = _u32(0, 1, 2, 0, 2, 3)
    entry["uv"] = _f32(0, 0, 1, 0, 1, 1, 0, 1)
    key = entry["tex_key"]
    payload["textures"][key] = f"{frontend_url}/delayed.png"
    texture_uri = _flat_png_uri((236, 42, 38, 255))
    pending = {"requests": 0, "route": None}

    context, page = _page(edge_browser, frontend_url, {"Delayed": payload})

    def hold(route):
        pending["requests"] += 1
        pending["route"] = route

    page.route("**/delayed.png", hold)
    try:
        _open(page, "Delayed")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("""
          () => {
            const mesh = window.modViewer.activeMeshes[0];
            return mesh?.material?.userData?.gameMaterial
              ?.bindings?.diffuse?.enabledNode?.value === false;
          }
        """)
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        state = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const game = mesh.material.userData.gameMaterial;
          return {
            diffuseEnabled: game.bindings.diffuse.enabledNode.value,
            fallbackColor: mesh.material.color.getHex(),
          };
        }""")
        assert pending["requests"] == 1
        assert state == {"diffuseEnabled": False, "fallbackColor": 0xcccccc}
        pending_pixel = _sample_mesh_pixel(page)
        assert min(pending_pixel) > 20
        assert max(pending_pixel) - min(pending_pixel) < 30

        idle_count = page.evaluate("window.modViewer.getRenderCount()")
        page.wait_for_timeout(200)
        assert page.evaluate("window.modViewer.getRenderCount()") == idle_count

        page.evaluate("window.modViewer.setEnvironmentPreset('studio')")
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() > count", arg=idle_count)
        changed_count = page.evaluate("window.modViewer.getRenderCount()")

        pending["route"].fulfill(
            status=200,
            content_type="image/png",
            body=base64.b64decode(texture_uri.split(",", 1)[1]),
        )
        page.wait_for_function("""
          () => {
            const binding = window.modViewer.activeMeshes[0]?.material
              ?.userData?.gameMaterial?.bindings?.diffuse;
            return binding?.enabledNode?.value === true
              && binding.textureNode.value?.image?.width === 4;
          }
        """)
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() > count", arg=changed_count)
        loaded_pixel = _sample_mesh_pixel(page)
        assert loaded_pixel[0] > loaded_pixel[1] * 1.5, loaded_pixel
        assert loaded_pixel != pending_pixel, (pending_pixel, loaded_pixel)
    finally:
        context.close()

def test_view_gizmo_snap_renders_only_during_animation(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"Snap": _payload("Snap")})
    try:
        _open(page, "Snap")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        page.wait_for_timeout(200)
        idle_count = page.evaluate("window.modViewer.getRenderCount()")

        page.evaluate("""
          () => document.querySelector('.gizmo-axis.positive')
            .dispatchEvent(new KeyboardEvent('keydown', {
              key: 'Enter', bubbles: true,
            }))
        """)
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() >= count + 2",
            arg=idle_count)
        page.wait_for_timeout(350)
        settled_count = page.evaluate("window.modViewer.getRenderCount()")
        page.wait_for_timeout(200)
        assert page.evaluate("window.modViewer.getRenderCount()") == settled_count
        assert settled_count > idle_count + 2
    finally:
        context.close()

def test_nonfinite_mesh_positions_do_not_poison_camera_fit(
        edge_browser, frontend_url):
    payload = _payload("InvalidGeometry")
    entry = payload["meshes"]["Body-InvalidGeometry-0"]
    entry["pos"] = _f32(
        0, 0, 0, 1, 0, 0, 0, 1, 0,
        float("nan"), float("nan"), float("nan"),
    )
    entry["idx"] = _u32(0, 1, 2)
    context, page = _page(
        edge_browser, frontend_url, {"InvalidGeometry": payload})
    try:
        _open(page, "InvalidGeometry")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        camera = page.evaluate("""async () => {
          const {camera, controls} = await import('./js/scene/scene.js');
          return {
            position: camera.position.toArray(),
            target: controls.target.toArray(),
            near: camera.near,
            far: camera.far,
          };
        }""")
        assert all(math.isfinite(value)
                   for value in camera["position"] + camera["target"])
        assert math.isfinite(camera["near"])
        assert math.isfinite(camera["far"])
    finally:
        context.close()

def test_shared_texture_waits_once_and_updates_all_meshes(
        edge_browser, frontend_url):
    payload = _payload("Shared")
    entry = payload["meshes"]["Body-Shared-0"]
    entry["uv"] = _f32(0, 0, 1, 0, 0, 1)
    second = copy.deepcopy(entry)
    second["component"] = "Face Shared"
    payload["meshes"]["Face-Shared-0"] = second
    key = entry["tex_key"]
    payload["textures"][key] = f"{frontend_url}/shared.png"
    pending = {"requests": 0, "route": None}

    context, page = _page(edge_browser, frontend_url, {"Shared": payload})

    def hold(route):
        pending["requests"] += 1
        pending["route"] = route

    page.route("**/shared.png", hold)
    try:
        _open(page, "Shared")
        page.wait_for_function("window.modViewer.activeMeshes.length === 2")
        page.wait_for_function("""
          () => window.modViewer.activeMeshes.every(mesh =>
            mesh.material.userData.gameMaterial.bindings.diffuse.enabledNode.value
              === false)
        """)
        assert pending["requests"] == 1

        pending["route"].fulfill(
            status=200,
            content_type="image/png",
            body=base64.b64decode(_PNG_URI.split(",", 1)[1]),
        )
        page.wait_for_function("""
          () => window.modViewer.activeMeshes.every(mesh =>
            mesh.material.userData.gameMaterial.bindings.diffuse.enabledNode.value
              === true)
        """)
    finally:
        context.close()

@pytest.mark.parametrize(("profile_id", "normal_role"), [
    ("zzz:zzmi", "normal_map"),
    ("wuwa:rabbitfx:body", "normal_data"),
])
def test_diffuse_normal_mode_keeps_only_color_and_normal_bindings(
        edge_browser, frontend_url, profile_id, normal_role):
    payload = _packed_material_payload(profile_id)
    entry = payload["meshes"]["Body-Packed-0"]
    if normal_role == "normal_map":
        normal_key = "normal_map::Packed-surface-normal.png"
        entry["normal_map_key"] = normal_key
        payload["textures"][normal_key] = _PNG_URI
    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function("""normalRole => {
          const bindings = window.modViewer.activeMeshes[0]?.material
            ?.userData?.gameMaterial?.bindings;
          return bindings?.diffuse.enabledNode.value === true
            && bindings[normalRole].enabledNode.value === true;
        }""", arg=normal_role)

        page.locator("#texture-btn").click()
        options = page.locator("#texture-popover .ui-popover-option")
        assert options.all_inner_texts() == [
            "All maps", "Diffuse and NormalMap", "Diffuse only", "No textures"]
        options.filter(has_text="Diffuse and NormalMap").click()
        bindings = page.evaluate("""() => {
          const values = window.modViewer.activeMeshes[0].material
            .userData.gameMaterial.bindings;
          return Object.fromEntries(Object.entries(values).map(
            ([role, binding]) => [role, binding.enabledNode.value]));
        }""")
        expected_bindings = {
            "diffuse": True,
            "normal_map": False,
            "normal_data": False,
            "light_map": False,
            "material_map": False,
            "emission_map": False,
        }
        expected_bindings[normal_role] = True
        assert bindings == expected_bindings
        button = page.locator("#texture-btn")
        assert "diffuse-normal" in (button.get_attribute("class") or "")
        assert button.get_attribute("aria-label") == (
            "Textures: diffuse and normal map")
        assert button.evaluate("button => getComputedStyle(button).color") == (
            "rgb(242, 204, 114)")
    finally:
        context.close()

def test_failed_texture_stays_fallback_without_retrying(
        edge_browser, frontend_url):
    payload = _payload("FailedTexture")
    entry = payload["meshes"]["Body-FailedTexture-0"]
    entry["uv"] = _f32(0, 0, 1, 0, 0, 1)
    key = entry["tex_key"]
    payload["textures"][key] = f"{frontend_url}/failed.png"
    pending = {"requests": 0, "route": None}

    context, page = _page(
        edge_browser, frontend_url, {"FailedTexture": payload})

    def hold(route):
        pending["requests"] += 1
        pending["route"] = route

    page.route("**/failed.png", hold)
    try:
        _open(page, "FailedTexture")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.bindings?.diffuse?.enabledNode?.value === false
        """)
        pending["route"].abort()
        page.wait_for_function(
            """async key => {
              const {hasTexture} = await import('./js/mesh/mesh-factory.js');
              return hasTexture(key) === false;
            }""",
            arg=key,
        )
        page.wait_for_timeout(100)
        assert pending["requests"] == 1
        assert page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          return {
            enabled: mesh.material.userData.gameMaterial
              .bindings.diffuse.enabledNode.value,
            fallbackColor: mesh.material.color.getHex(),
          };
        }""") == {"enabled": False, "fallbackColor": 0xcccccc}
    finally:
        context.close()

def test_native_dds_failure_falls_back_without_black_frame(
        edge_browser, frontend_url):
    payload = _payload("NativeDDS")
    entry = payload["meshes"]["Body-NativeDDS-0"]
    entry["uv"] = _f32(0, 0, 1, 0, 0, 1)
    key = "diffuse::NativeDDS.dds"
    entry["tex_key"] = key
    payload["textures"] = {key: f"{frontend_url}/native.dds"}
    pending = {"dds": None}
    requests = {"dds": 0, "png": 0}

    context, page = _page(edge_browser, frontend_url, {"NativeDDS": payload})
    try:
        supported = page.evaluate("""
          async () => {
            const {supportsBCTextureCompression} =
              await import('./js/scene/renderer-capabilities.js');
            return supportsBCTextureCompression();
          }
        """)
        if not supported:
            pytest.skip("native DDS is not supported by the test renderer")

        def hold_dds(route):
            requests["dds"] += 1
            pending["dds"] = route

        def fulfill_png(route):
            requests["png"] += 1
            route.fulfill(
                status=200,
                content_type="image/png",
                body=base64.b64decode(_PNG_URI.split(",", 1)[1]),
            )

        page.route("**/native.dds", hold_dds)
        page.route("**/native.png", fulfill_png)
        _open(page, "NativeDDS")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.bindings?.diffuse?.enabledNode?.value === false
        """)
        assert requests == {"dds": 1, "png": 0}

        pending["dds"].abort()
        page.wait_for_function("""
          () => {
            const binding = window.modViewer.activeMeshes[0]?.material
              ?.userData?.gameMaterial?.bindings?.diffuse;
            return binding?.enabledNode?.value === true
              && binding.textureNode.value?.isCompressedTexture !== true
              && binding.textureNode.value?.image?.width === 1;
          }
        """)
        assert requests == {"dds": 1, "png": 1}
    finally:
        context.close()

def test_replaced_pending_texture_ignores_stale_completion(
        edge_browser, frontend_url):
    payload = _payload("Replacement")
    entry = payload["meshes"]["Body-Replacement-0"]
    entry["uv"] = _f32(0, 0, 1, 0, 0, 1)
    key = entry["tex_key"]
    payload["textures"][key] = f"{frontend_url}/old.png"
    pending = {"old": None}
    requests = {"old": 0, "new": 0}

    context, page = _page(edge_browser, frontend_url, {"Replacement": payload})
    try:
        def hold_old(route):
            requests["old"] += 1
            pending["old"] = route

        def fulfill_new(route):
            requests["new"] += 1
            route.fulfill(
                status=200,
                content_type="image/png",
                body=base64.b64decode(_PNG_URI.split(",", 1)[1]),
            )

        page.route("**/old.png", hold_old)
        page.route("**/new.png", fulfill_new)
        _open(page, "Replacement")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.bindings?.diffuse?.enabledNode?.value === false
        """)
        assert requests == {"old": 1, "new": 0}

        page.evaluate("""async ({key, uri}) => {
          const {addTexture, refreshMeshTexture} =
            await import('./js/mesh/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          addTexture(key, uri);
          refreshMeshTexture(mesh);
        }""", {"key": key, "uri": f"{frontend_url}/new.png"})
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.bindings?.diffuse?.enabledNode?.value === true
        """)
        assert requests == {"old": 1, "new": 1}
        page.evaluate("""() => {
          window.__replacementTexture = window.modViewer.activeMeshes[0]
            .material.userData.gameMaterial.bindings.diffuse.textureNode.value;
        }""")

        pending["old"].fulfill(
            status=200,
            content_type="image/png",
            body=base64.b64decode(_flat_png_uri((255, 0, 0, 255))
                                 .split(",", 1)[1]),
        )
        page.wait_for_timeout(200)
        assert page.evaluate("""
          () => {
            const binding = window.modViewer.activeMeshes[0].material
              .userData.gameMaterial.bindings.diffuse;
            return binding.enabledNode.value === true
              && binding.textureNode.value === window.__replacementTexture;
          }
        """)
    finally:
        context.close()

def test_webgpu_unsupported_state_is_visible_and_never_falls_back(
        edge_browser, frontend_url):
    context = edge_browser.new_context(bypass_csp=True)
    context.add_init_script("""
      Object.defineProperty(Navigator.prototype, 'gpu', {
        configurable: true, get: () => undefined,
      });
      window.__webglFallbackRequested = false;
      const getContext = HTMLCanvasElement.prototype.getContext;
      HTMLCanvasElement.prototype.getContext = function(type, ...args) {
        if (type === 'webgl' || type === 'webgl2') {
          window.__webglFallbackRequested = true;
        }
        return getContext.call(this, type, ...args);
      };
    """)
    page = context.new_page()
    try:
        page.goto(frontend_url)
        page.locator("#renderer-error.show").wait_for(timeout=5000)
        assert "WebGPU is required" in page.locator("#renderer-error").inner_text()
        assert page.locator("#open-btn").is_disabled()
        assert not page.evaluate("window.__webglFallbackRequested")
    finally:
        context.close()

def test_packed_material_profile_uses_tsl_nodes_and_stable_bindings(
        edge_browser, frontend_url):
    profile_id = "zzz:zzmi"
    context, page = _page(
        edge_browser, frontend_url,
        {"Packed": _packed_material_payload(profile_id)},
    )
    runtime_errors = []
    page.on("pageerror", lambda error: runtime_errors.append(str(error)))
    page.on("console", lambda message: runtime_errors.append(message.text)
            if message.type == "error" and "Failed to load resource" not in message.text
            else None)
    try:
        _open(page, "Packed")
        page.locator(".draw-item").wait_for()
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial")
        packed_role = "material_map" if profile_id == "zzz:zzmi" else "light_map"
        page.wait_for_function(
            f"window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            f"?.bindings?.{packed_role}?.enabledNode?.value")

        state = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          const game = material.userData.gameMaterial;
              const packedRole = game.profile.id.startsWith('zzz') ? 'material_map' : 'light_map';
              const lightingModel = material.setupLightingModel();
              window.__shadowLevelNode = game.shadowLevelNode;
              window.__shadowThresholdNode = game.shadowThresholdNode;
              return {
            physical: material.isMeshPhysicalNodeMaterial,
            profile: game.profile.id,
            hasGeneratedShaderState: Object.hasOwn(game, 'shader'),
            hasLegacyUniformState: Object.hasOwn(game, 'uniforms'),
            lightMap: game.bindings.light_map.enabledNode.value,
            materialMap: game.bindings.material_map.enabledNode.value,
            sampledRole: game.bindings[packedRole].textureNode.value
              !== game.bindings[packedRole].placeholder,
            hasMetalnessNode: !!material.metalnessNode,
            hasSpecularResponseNode: !!game.specularResponseNode,
            lightingModel: lightingModel.constructor.name,
            metalnessScale: game.profile.metalness_scale,
            specularScale: game.profile.specular_scale,
            specularInfluence: game.profile.specular_influence,
            shadowThreshold: game.shadowThresholdNode.value,
            shadowSoftness: game.shadowSoftnessNode.value,
            shadowLevel: game.shadowLevelNode.value,
            shadowMaskStrength: game.shadowMaskStrengthNode.value,
            shadowInfluence: game.shadowInfluenceNode.value,
            materialKind: mesh.userData.materialKind,
            materialKindReliable: mesh.userData.materialKindReliable,
            materialProfileId: mesh.userData.materialProfileId,
            version: material.version,
          };
        }""")
        assert state["physical"]
        assert state["profile"] == profile_id
        assert not state["hasGeneratedShaderState"]
        assert not state["hasLegacyUniformState"]
        assert state["metalnessScale"] == (1 if profile_id == "zzz:zzmi" else 0.08)
        assert state["specularScale"] == 1
        assert state["specularInfluence"] == (None if profile_id == "zzz:zzmi" else 0.15)
        assert (state["shadowThreshold"], state["shadowSoftness"],
                state["shadowLevel"], state["shadowMaskStrength"],
                state["shadowInfluence"]) == (0.5, 0.04, 0.35, 0.5, 0.45)
        assert (state["materialKind"], state["materialKindReliable"],
                state["materialProfileId"]) == ("body", False, profile_id)
        assert state["lightingModel"] == (
            "GenshinLightingModel" if profile_id == "genshin:gimi"
            else "ZzzLightingModel")
        assert state["hasSpecularResponseNode"]
        assert state["sampledRole"]
        assert not runtime_errors, "\n".join(runtime_errors)
        assert state["lightMap"] and state["materialMap"] \
            if profile_id == "zzz:zzmi" else state["lightMap"]

        after = page.evaluate("""async () => {
          const {setMeshTextureState} = await import('./js/mesh/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          const game = material.userData.gameMaterial;
          const packedRole = game.profile.id.startsWith('zzz') ? 'material_map' : 'light_map';
          const packedNode = game.bindings[packedRole].textureNode;
          const packedEnabled = game.bindings[packedRole].enabledNode;
          const version = material.version;
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: mesh.userData.normalMapKey,
            normal_data: null,
            light_map: null,
            material_map: null,
          });
          return {
            sameTextureNode: game.bindings[packedRole].textureNode === packedNode,
            sameEnabledNode: game.bindings[packedRole].enabledNode === packedEnabled,
            sameShadowLevelNode: game.shadowLevelNode === window.__shadowLevelNode,
            sameShadowThresholdNode: game.shadowThresholdNode === window.__shadowThresholdNode,
            sameVersion: material.version === version,
            mapEnabled: packedEnabled.value,
            usesPlaceholder: game.bindings[packedRole].textureNode.value
              === game.bindings[packedRole].placeholder,
          };
        }""")
        assert after == {"sameTextureNode": True, "sameEnabledNode": True,
                         "sameShadowLevelNode": True,
                         "sameShadowThresholdNode": True,
                         "sameVersion": True, "mapEnabled": False,
                         "usesPlaceholder": True}
    finally:
        context.close()

def test_each_mesh_resolves_its_own_profile_and_packed_source(
        edge_browser, frontend_url):
    payload = _packed_material_payload("zzz:zzmi")
    body_name, body = next(iter(payload["meshes"].items()))
    face_name = "Face-Packed-0"
    face = copy.deepcopy(body)
    face["component"] = "Face Packed"
    face["material_kind"] = "face"
    face["material_kind_reliable"] = False
    face["material_profile_id"] = "genshin:gimi"
    payload["meshes"] = {body_name: body, face_name: face}
    payload["metadata"]["material_profiles"]["genshin:gimi"] = \
        material_profile_for("genshin", "gimi").to_metadata()

    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes.length === 2 && "
            "window.modViewer.activeMeshes.every(mesh => "
            "mesh.material.userData.gameMaterial)")
        states = page.evaluate("""() => window.modViewer.activeMeshes.map(mesh => ({
          profileId: mesh.userData.materialProfileId,
          kind: mesh.userData.materialKind,
          lightMap: mesh.material.userData.gameMaterial.bindings.light_map.enabledNode.value,
          materialMap: mesh.material.userData.gameMaterial.bindings.material_map.enabledNode.value,
        }))""")
        assert states == [
            {"profileId": "zzz:zzmi", "kind": "body",
             "lightMap": True, "materialMap": True},
            {"profileId": "genshin:gimi", "kind": "face",
             "lightMap": True, "materialMap": False},
        ]
        assert page.evaluate("window.modViewer.getMaterialState(1).profileId") == (
            "genshin:gimi")
    finally:
        context.close()

def test_genshin_no_uv_keeps_a_and_b_out_of_conservative_material_graph(
        edge_browser, frontend_url):
    payload = _packed_material_payload("genshin:gimi")
    payload["meshes"]["Body-Packed-0"].pop("uv")
    uv_messages = []
    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    page.on("console", lambda message: uv_messages.append(message.text)
            if "uv" in message.text.lower() else None)
    try:
        _open(page, "Packed")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        state = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          const game = material.userData.gameMaterial;
          window.__rawMaterial = material;
          window.__rawColorNode = material.colorNode;
          return {
            standard: !!material.isMeshStandardNodeMaterial,
            physical: !!material.isMeshPhysicalNodeMaterial,
            packedResponse: game.packedResponse,
            hasMaterialId: game.hasMaterialId,
            hasSpecularArea: game.hasSpecularArea,
            hasShadowMask: game.hasShadowMask,
            lightMap: game.bindings.light_map.enabledNode.value,
            lightingModel: material.setupLightingModel().constructor.name,
          };
        }""")
        assert state == {
            "standard": True, "physical": False,
            "packedResponse": False,
            "hasMaterialId": False, "hasSpecularArea": False,
            "hasShadowMask": False, "lightMap": False,
            "lightingModel": "GenshinLightingModel",
        }
        assert not uv_messages, uv_messages
    finally:
        context.close()


def test_rabbitfx_emission_binding_stays_stable_across_texture_replacement(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx")
    entry = payload["meshes"]["Body-Packed-0"]
    entry["emission_map_key"] = "emission_map::Packed-glow.png"
    payload["textures"]["emission_map::Packed-glow.png"] = _PNG_URI
    context, page = _page(edge_browser, frontend_url, {"Emission": payload})
    try:
        _open(page, "Emission")
        page.wait_for_function("""() => {
          const game = window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial;
          return game?.bindings?.emission_map?.enabledNode?.value === true;
        }""")
        state = page.evaluate("""async () => {
          const {setMeshTextureState} = await import('./js/mesh/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          const game = material.userData.gameMaterial;
          const binding = game.bindings.emission_map;
          const textureNode = binding.textureNode;
          const enabledNode = binding.enabledNode;
          const emissiveNode = material.emissiveNode;
          const version = material.version;
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: mesh.userData.normalMapKey,
            normal_data: mesh.userData.normalDataKey,
            light_map: mesh.userData.lightMapKey,
            material_map: mesh.userData.materialMapKey,
            emission_map: null,
          });
          const disabled = binding.enabledNode.value === false;
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: mesh.userData.normalMapKey,
            normal_data: mesh.userData.normalDataKey,
            light_map: mesh.userData.lightMapKey,
            material_map: mesh.userData.materialMapKey,
            emission_map: 'emission_map::Packed-glow.png',
          });
          return {
            profile: game.profile.emission_source,
            rebound: binding.enabledNode.value === true,
            disabled,
            sameTextureNode: binding.textureNode === textureNode,
            sameEnabledNode: binding.enabledNode === enabledNode,
            sameEmissiveNode: material.emissiveNode === emissiveNode,
            sameVersion: material.version === version,
          };
        }""")
        assert state == {
            "profile": "emission_map_rgb", "disabled": True, "rebound": True,
            "sameTextureNode": True, "sameEnabledNode": True,
            "sameEmissiveNode": True, "sameVersion": True,
        }
    finally:
        context.close()


def test_rabbitfx_emission_produces_selective_bloom_output(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx")
    entry = payload["meshes"]["Body-Packed-0"]
    entry["pos"] = _f32(-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0)
    entry["idx"] = _u32(0, 1, 2, 0, 2, 3)
    entry["drawindexed"] = [6, 0, 0]
    entry["emission_map_key"] = "emission_map::Packed-glow.png"
    payload["textures"][entry["tex_key"]] = _flat_png_uri((0, 0, 0, 255))
    payload["textures"]["emission_map::Packed-glow.png"] = _flat_png_uri(
        (255, 255, 255, 255))
    context, page = _page(edge_browser, frontend_url, {"EmissionBloom": payload})
    try:
        _open(page, "EmissionBloom")
        page.wait_for_function("""() => window.modViewer.getMaterialState(0)
          .emissionMapBound""")
        canvas = page.locator("#canvas-container canvas")
        without_bloom = Image.open(io.BytesIO(canvas.screenshot())).convert("RGB")
        page.evaluate("window.modViewer.setBloomEnabled(true)")
        page.wait_for_function("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          const state = getViewportRenderPipelineDebugState();
          return state.activeRenderMode === 'bloom' && state.bloomOnlyRenderCount > 0;
        }""")
        with_bloom = Image.open(io.BytesIO(canvas.screenshot())).convert("RGB")
        assert ImageChops.difference(without_bloom, with_bloom).getbbox()
    finally:
        context.close()


@pytest.mark.parametrize("has_uv", [True, False])
def test_zzz_toon_lighting_works_without_light_or_material_maps(
        edge_browser, frontend_url, has_uv):
    payload = _packed_material_payload("zzz:zzmi")
    entry = payload["meshes"]["Body-Packed-0"]
    if not has_uv:
        entry.pop("uv")
    entry["light_map_key"] = None
    entry["material_map_key"] = None
    entry["normal_data_key"] = None
    payload["textures"] = {
        "diffuse::Packed-one.png": _flat_png_uri((96, 96, 96, 255)),
    }

    def render(test_payload, enable_toon):
        context, page = _page(edge_browser, frontend_url,
                               {"Packed": test_payload})
        try:
            _open(page, "Packed")
            page.wait_for_function(
                "window.modViewer.activeMeshes[0]?.material?.userData"
                "?.gameMaterial")
            _set_test_key_light(page)
            if enable_toon:
                page.locator("#toon-btn").click()
                page.wait_for_function(
                    "window.modViewer.activeMeshes[0].material.userData"
                    ".gameMaterial.toonEnabledNode.value === true")
            state = page.evaluate("""() => {
              const mesh = window.modViewer.activeMeshes[0];
              const material = mesh.material;
              const game = material.userData.gameMaterial;
              return {
                model: material.setupLightingModel().constructor.name,
                lightMap: game.bindings.light_map.enabledNode.value,
                materialMap: game.bindings.material_map.enabledNode.value,
                shadowLevel: game.shadowLevelNode.value,
              };
            }""")
            return state, _sample_mesh_pixel(page)
        finally:
            context.close()

    toon_state, toon_pixel = render(payload, True)
    physical_payload = copy.deepcopy(payload)
    physical_payload["metadata"]["material_profiles"]["zzz:zzmi"] = (
        material_profile_for("zzz", "zzmi").to_metadata())
    physical_payload["metadata"]["material_profiles"]["zzz:zzmi"] = {
        **physical_payload["metadata"]["material_profiles"]["zzz:zzmi"],
        "direct_shadow_model": None,
    }
    physical_state, physical_pixel = render(physical_payload, False)

    assert toon_state == {
        "model": "ZzzLightingModel", "lightMap": False,
        "materialMap": False, "shadowLevel": 0.35,
    }
    assert physical_state["model"] == "PhysicalLightingModel"
    assert sum(toon_pixel) > sum(physical_pixel) + 8, (
        toon_pixel, physical_pixel)


def test_genshin_toon_uses_n_dot_l_when_light_map_is_missing(
        edge_browser, frontend_url):
    base = _packed_material_payload("genshin:gimi")
    base_entry = base["meshes"]["Body-Packed-0"]
    light_key = base_entry["light_map_key"]
    base["textures"] = {
        "diffuse::Packed-one.png": _flat_png_uri((96, 96, 96, 255)),
    }

    def make_payload(green):
        payload = copy.deepcopy(base)
        entry = payload["meshes"]["Body-Packed-0"]
        if green is None:
            entry["light_map_key"] = None
        else:
            entry["light_map_key"] = light_key
            payload["textures"][light_key] = _flat_png_uri(
                (0, green, 0, 255))
        return payload

    def render(test_payload):
        context, page = _page(edge_browser, frontend_url,
                               {"Packed": test_payload})
        try:
            _open(page, "Packed")
            page.wait_for_function(
                "window.modViewer.activeMeshes[0]?.material?.userData"
                "?.gameMaterial")
            _set_test_key_light(page)
            page.locator("#toon-btn").click()
            page.wait_for_function(
                "window.modViewer.activeMeshes[0].material.userData"
                ".gameMaterial.toonEnabledNode.value === true")
            state = page.evaluate("""() => {
              const mesh = window.modViewer.activeMeshes[0];
              const material = mesh.material;
              const game = material.userData.gameMaterial;
              return {
                model: material.setupLightingModel().constructor.name,
                lightMap: game.bindings.light_map.enabledNode.value,
                hasShadowMask: game.hasShadowMask,
                shadowLevel: game.shadowLevelNode.value,
              };
            }""")
            return state, _sample_mesh_pixel(page)
        finally:
            context.close()

    missing_state, missing_pixel = render(make_payload(None))
    neutral_state, neutral_pixel = render(make_payload(128))
    shadow_state, shadow_pixel = render(make_payload(0))

    assert missing_state == {
        "model": "GenshinLightingModel", "lightMap": False,
        "hasShadowMask": True, "shadowLevel": 0.35,
    }
    assert neutral_state["lightMap"]
    assert neutral_state["hasShadowMask"]
    assert max(abs(a - b) for a, b in zip(missing_pixel, neutral_pixel)) <= 3, (
        missing_pixel, neutral_pixel)
    assert sum(shadow_pixel) + 8 < sum(neutral_pixel), (
        shadow_pixel, neutral_pixel)


def test_toon_shadow_toggle_uses_stable_uniform_and_inherits_to_new_meshes(
        edge_browser, frontend_url):
    payload = _packed_material_payload("zzz:zzmi")
    entry = payload["meshes"]["Body-Packed-0"]
    entry.pop("uv")
    entry["light_map_key"] = None
    entry["material_map_key"] = None
    entry["normal_data_key"] = None
    payload["textures"] = {
        "diffuse::Packed-one.png": _flat_png_uri((96, 96, 96, 255)),
    }
    context, page = _page(
        edge_browser, frontend_url,
        {"A": payload, "B": copy.deepcopy(payload)},
    )
    try:
        _open(page, "A")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData"
            "?.gameMaterial?.toonEnabledNode")
        _set_test_key_light(page)
        before = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          const game = material.userData.gameMaterial;
          window.__toonMaterial = material;
          window.__toonEnabledNode = game.toonEnabledNode;
          return {
            material,
            version: material.version,
            enabled: game.toonEnabledNode.value,
            pixel: null,
          };
        }""")
        before_pixel = _sample_mesh_pixel(page)
        assert before["enabled"] is False
        assert page.locator("#toon-btn").get_attribute("aria-label") == (
            "Toon shadows: off")
        assert page.locator("#toon-btn").get_attribute("aria-pressed") == "false"
        assert "off" in (page.locator("#toon-btn").get_attribute("class") or "")

        render_count = page.evaluate("window.modViewer.getRenderCount()")
        page.locator("#toon-btn").click()
        page.wait_for_function(
            "window.modViewer.activeMeshes[0].material.userData.gameMaterial"
            ".toonEnabledNode.value === true")
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() > count", arg=render_count)
        page.wait_for_timeout(250)
        toon_pixel = _sample_mesh_pixel(page)
        toon = page.evaluate("""version => {
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          const game = material.userData.gameMaterial;
          return {
            sameMaterial: material === window.__toonMaterial,
            sameNode: game.toonEnabledNode === window.__toonEnabledNode,
            sameVersion: material.version === version,
            enabled: game.toonEnabledNode.value,
          };
        }""", before["version"])
        assert toon == {
            "sameMaterial": True, "sameNode": True, "sameVersion": True,
            "enabled": True,
        }
        assert page.locator("#toon-btn").get_attribute("aria-label") == (
            "Toon shadows: on")
        assert page.locator("#toon-btn").get_attribute("aria-pressed") == "true"
        assert "active" in (page.locator("#toon-btn").get_attribute("class") or "")
        assert sum(before_pixel) != sum(toon_pixel), (before_pixel, toon_pixel)

        page.locator("#toon-btn").click()
        page.wait_for_function(
            "window.modViewer.activeMeshes[0].material.userData.gameMaterial"
            ".toonEnabledNode.value === false")
        page.wait_for_timeout(250)
        off_again_pixel = _sample_mesh_pixel(page)
        assert off_again_pixel == pytest.approx(before_pixel, abs=2)

        _open(page, "B")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData"
            "?.gameMaterial?.toonEnabledNode")
        inherited = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          return mesh.material.userData.gameMaterial.toonEnabledNode.value;
        }""")
        assert inherited is False
    finally:
        context.close()


def test_toon_shadow_off_ignores_genshin_light_map_boundary_refinement(
        edge_browser, frontend_url):
    payload = _packed_material_payload("genshin:gimi")
    entry = payload["meshes"]["Body-Packed-0"]
    low_key = "light_map::Packed-toon-low.png"
    high_key = "light_map::Packed-toon-high.png"
    payload["textures"] = {
        "diffuse::Packed-one.png": _flat_png_uri((96, 96, 96, 255)),
        low_key: _flat_png_uri((0, 0, 0, 255)),
        high_key: _flat_png_uri((0, 255, 0, 255)),
    }
    entry["light_map_key"] = low_key
    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.bindings?.light_map?.enabledNode?.value === true
        """)
        _set_test_key_light(page)
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.toonEnabledNode?.value === false
        """)
        page.wait_for_timeout(250)
        low_pixel = _sample_mesh_pixel(page)
        page.evaluate("""async key => {
          const {setMeshTextureState} = await import('./js/mesh/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: mesh.userData.normalMapKey,
            normal_data: null,
            light_map: key,
            material_map: null,
          });
        }""", high_key)
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.bindings?.light_map?.textureNode?.value?.image
            ?.width === 4
        """)
        page.wait_for_timeout(250)
        high_pixel = _sample_mesh_pixel(page)
        assert max(abs(a - b) for a, b in zip(low_pixel, high_pixel)) <= 3, (
            low_pixel, high_pixel)
    finally:
        context.close()


@pytest.mark.parametrize("profile_id", ["wuwa:rabbitfx", "wuwa:rabbitfx:body"])
def test_wuwa_toon_shadow_toggle_uses_stable_uniform(
        edge_browser, frontend_url, profile_id):
    payload = _packed_material_payload(profile_id)
    entry = payload["meshes"]["Body-Packed-0"]
    shadow_key = "light_map::Packed-wuwa-shadow.png"
    payload["textures"] = {
        "diffuse::Packed-one.png": _flat_png_uri((96, 96, 96, 255)),
        entry["normal_data_key"]: _flat_png_uri((128, 128, 255, 255)),
        shadow_key: _flat_png_uri((0, 16, 0, 255)),
    }
    entry["light_map_key"] = shadow_key

    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.bindings?.light_map?.enabledNode?.value === true
        """)
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.bindings?.light_map?.textureNode?.value?.image
            ?.width === 4
        """)
        _set_test_key_light(page)
        before = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          const game = material.userData.gameMaterial;
          window.__wuwaToonMaterial = material;
          window.__wuwaToonEnabledNode = game.toonEnabledNode;
          return {
            profile: game.profile.id,
            model: material.setupLightingModel().constructor.name,
            version: material.version,
            enabled: game.toonEnabledNode.value,
          };
        }""")
        off_pixel = _sample_mesh_pixel(page)

        page.locator("#toon-btn").click()
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0].material.userData
            .gameMaterial.toonEnabledNode.value === true
        """)
        page.wait_for_timeout(250)
        toon_pixel = _sample_mesh_pixel(page)
        toon = page.evaluate("""version => {
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          const game = material.userData.gameMaterial;
          return {
            sameMaterial: material === window.__wuwaToonMaterial,
            sameNode: game.toonEnabledNode === window.__wuwaToonEnabledNode,
            sameVersion: material.version === version,
            enabled: game.toonEnabledNode.value,
          };
        }""", before["version"])

        page.locator("#toon-btn").click()
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0].material.userData
            .gameMaterial.toonEnabledNode.value === false
        """)
        page.wait_for_timeout(250)
        off_again_pixel = _sample_mesh_pixel(page)

        assert before["profile"] == profile_id
        assert before["model"] == (
            "WuwaBodyLightingModel"
            if profile_id.endswith(":body") else "WuwaLightingModel")
        assert before["enabled"] is False
        assert toon == {
            "sameMaterial": True, "sameNode": True,
            "sameVersion": True, "enabled": True,
        }
        assert sum(off_pixel) > sum(toon_pixel) + 5, (off_pixel, toon_pixel)
        assert off_again_pixel == pytest.approx(off_pixel, abs=2)
    finally:
        context.close()


def test_wuwa_debug_modes_are_capability_gated_and_uniform_only(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx")
    entry = payload["meshes"]["Body-Packed-0"]
    normal_low = "normal_data::Packed-debug-low.png"
    normal_high = "normal_data::Packed-debug-high.png"
    entry["normal_data_key"] = normal_low
    payload["textures"][normal_low] = _flat_png_uri((0, 0, 0, 255))
    payload["textures"][normal_high] = _flat_png_uri((0, 0, 255, 255))
    context, page = _page(
        edge_browser, frontend_url,
        {"Packed": payload},
    )
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial")
        state = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const material = mesh.material;
          const game = material.userData.gameMaterial;
          const colorNode = material.colorNode;
          const version = material.version;
          const before = window.modViewer.getMaterialState(0);
          const modes = [
            window.modViewer.setMaterialDebugMode('material-id'),
            window.modViewer.getMaterialState(0).debugMode,
            window.modViewer.setMaterialDebugMode('normal-data-b'),
            window.modViewer.getMaterialState(0).debugMode,
            window.modViewer.setMaterialDebugMode('normal-data-a'),
            window.modViewer.getMaterialState(0).debugMode,
          ];
          return {
            before,
            modes,
            sameMaterial: material === mesh.material,
            sameColorNode: material.colorNode === colorNode,
            sameVersion: material.version === version,
            hasDebugNodes: !!game.normalDataBNode && !!game.normalDataANode,
          };
        }""")
        assert state["before"]["supportedDebugModes"] == [
            "shadow-mask", "normal-data-b", "normal-data-a"]
        assert state["before"]["hasShadowMask"] is True
        assert state["before"]["hasNormalData"] is True
        assert state["before"]["hasNormalDataB"] is True
        assert state["before"]["hasNormalDataA"] is True
        assert state["modes"] == [
            "material-id", "material-id", "normal-data-b", "normal-data-b",
            "normal-data-a", "normal-data-a",
        ]
        assert state["sameMaterial"]
        assert state["sameColorNode"]
        assert state["sameVersion"]
        assert state["hasDebugNodes"]

        page.evaluate("window.modViewer.setMaterialDebugMode('normal-data-b')")
        page.wait_for_function("""
          () => {
            const binding = window.modViewer.activeMeshes[0]?.material
              ?.userData?.gameMaterial?.bindings?.normal_data;
            return binding?.enabledNode?.value === true
              && binding.textureNode.value?.image?.width === 4;
          }
        """)
        page.wait_for_timeout(300)
        low_pixel = _sample_mesh_pixel(page)
        page.evaluate("""async key => {
          const {setMeshTextureState} = await import('./js/mesh/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          window.__normalLowTexture = mesh.material.userData.gameMaterial
            .bindings.normal_data.textureNode.value;
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: null,
            normal_data: key,
            light_map: null,
            material_map: null,
          });
        }""", normal_high)
        page.wait_for_function("""
          () => {
            const binding = window.modViewer.activeMeshes[0]?.material
              ?.userData?.gameMaterial?.bindings?.normal_data;
            return binding?.enabledNode?.value === true
              && binding.textureNode.value !== window.__normalLowTexture
              && binding.textureNode.value?.image?.width === 4;
          }
        """)
        page.wait_for_timeout(300)
        high_pixel = _sample_mesh_pixel(page)
        assert sum(high_pixel) > sum(low_pixel), (low_pixel, high_pixel)
    finally:
        context.close()

def test_wuwa_body_profile_binds_normal_data_without_stock_pbr_mapping(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx:body")
    entry = payload["meshes"]["Body-Packed-0"]
    entry["material_kind"] = "body"
    entry["material_kind_reliable"] = True
    entry["material_kind_reason"] = "viewer material-kind override"
    entry["material_kind_override"] = "body"
    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial")
        page.wait_for_timeout(300)
        page.locator("#inspector-tab").click()
        page.locator(".group-hdr .group-name").first.click()
        assert page.locator(".inspector-material-kind-control").count() == 1
        assert page.locator(".draw-item .material-kind-select").count() == 0
        layout = page.evaluate("""
          () => {
            const header = document.querySelector('.group-hdr');
            const name = header.querySelector('.group-name');
            const select = header.querySelector('.material-kind-select');
            const texture = header.querySelector('.group-tex-btn');
            return {
              hasMaterialLabel: !!header.querySelector('label'),
              hasMaterialControl: !!select,
              hasTextureControl: !!texture,
              nameTitle: name.title,
              nameOverflow: getComputedStyle(name).textOverflow,
              nameWhiteSpace: getComputedStyle(name).whiteSpace,
            };
          }
        """)
        assert layout == {
            "hasMaterialLabel": False,
            "hasMaterialControl": False,
            "hasTextureControl": False,
            "nameTitle": "Body Packed",
            "nameOverflow": "ellipsis",
            "nameWhiteSpace": "nowrap",
        }
        state = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const game = mesh.material.userData.gameMaterial;
          return {
            physical: !!mesh.material.isMeshPhysicalNodeMaterial,
            profileId: game.profile.id,
            directShadowModel: game.profile.direct_shadow_model,
            directSpecularModel: game.profile.direct_specular_model,
            materialKindOverride: window.modViewer.getMaterialState(0)
              .materialKindOverride,
            normalData: game.bindings.normal_data.enabledNode.value,
            lightMap: game.bindings.light_map.enabledNode.value,
            materialMap: game.bindings.material_map.enabledNode.value,
            normalSource: game.normalSource,
            normalPacking: game.normalPacking,
            normalMapKey: mesh.userData.normalMapKey,
            stockNormalMap: mesh.material.normalMap === null,
            stockMetalness: game.profile.metalness,
            stockSpecular: game.profile.specular,
            model: mesh.material.setupLightingModel().constructor.name,
          };
        }""")
        assert state == {
            "physical": True,
            "profileId": "wuwa:rabbitfx:body",
            "directShadowModel": "wuwa_base",
            "directSpecularModel": "wuwa_body",
            "materialKindOverride": "body",
            "normalData": True,
            "lightMap": True,
            "materialMap": False,
            "normalSource": "normal_data",
            "normalPacking": "rg",
            "normalMapKey": None,
            "stockNormalMap": True,
            "stockMetalness": None,
            "stockSpecular": None,
            "model": "WuwaBodyLightingModel",
        }
        _sample_mesh_pixel(page)
        before = page.evaluate("""() => {
          window.__bodyMaterial = window.modViewer.activeMeshes[0].material;
          return window.__bodyMaterial.version;
        }""")
        page.evaluate("""async () => {
          const {setMeshTextureState} = await import('./js/mesh/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: null,
            normal_data: null,
            light_map: mesh.userData.lightMapKey,
            material_map: null,
          });
        }""")
        page.wait_for_timeout(250)
        missing = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          return {
            bound: mesh.material.userData.gameMaterial
              .bindings.normal_data.enabledNode.value,
            sameMaterial: mesh.material === window.__bodyMaterial,
            version: mesh.material.version,
          };
        }""")
        assert missing["bound"] is False
        assert missing["sameMaterial"]
        assert missing["version"] == before
    finally:
        context.close()

def test_wuwa_body_b_threshold_controls_toon_classification(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx:body")
    entry = payload["meshes"]["Body-Packed-0"]
    low_key = "normal_data::Packed-body-b-low.png"
    high_key = "normal_data::Packed-body-b-high.png"
    payload["textures"] = {
        key: _flat_png_uri((255, 255, 255, 255))
        for key in payload["textures"]
    }
    payload["textures"]["diffuse::Packed-one.png"] = _flat_png_uri(
        (72, 72, 72, 255))
    payload["textures"][low_key] = _flat_png_uri((128, 128, 124, 0))
    payload["textures"][high_key] = _flat_png_uri((128, 128, 130, 0))
    entry["normal_data_key"] = low_key
    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings.normal_data.enabledNode.value === true")
        page.evaluate("window.modViewer.setEnvironmentPreset('studio')")
        page.evaluate("""
          async () => {
            const THREE = await import('three');
            const {scene, controls} = await import('./js/scene/scene.js');
            const {requestRender} = await import('./js/scene/render-scheduler.js');
            scene.traverse(object => {
              if (object.isAmbientLight || object.isHemisphereLight) object.intensity = 0;
              if (object.isSprite || object.isGridHelper) object.visible = false;
            });
            const key = scene.children.find(object => object.isDirectionalLight);
            key.target.position.copy(controls.target);
            key.position.copy(controls.target).add(new THREE.Vector3(0, 0, 3));
            key.intensity = 3;
            requestRender();
          }
        """)
        page.wait_for_timeout(400)
        low_pixels = [_sample_mesh_pixel_at(page, x, 0.1)
                      for x in (0.1, 0.3, 0.5, 0.7, 0.9)]
        page.evaluate("""async key => {
          const {setMeshTextureState} = await import('./js/mesh/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: null,
            normal_data: key,
            light_map: mesh.userData.lightMapKey,
            material_map: null,
          });
        }""", high_key)
        page.wait_for_timeout(400)
        high_pixels = [_sample_mesh_pixel_at(page, x, 0.1)
                       for x in (0.1, 0.3, 0.5, 0.7, 0.9)]
        assert any(sum(high) != sum(low)
                   for low, high in zip(low_pixels, high_pixels)), (
                       low_pixels, high_pixels)
    finally:
        context.close()

def test_wuwa_body_missing_toon_mask_keeps_physical_direct_specular(
        edge_browser, frontend_url):
    body_payload = _packed_material_payload("wuwa:rabbitfx:body")
    entry = body_payload["meshes"]["Body-Packed-0"]
    body_payload["textures"] = {
        key: _flat_png_uri((255, 255, 255, 255))
        for key in body_payload["textures"]
    }
    body_payload["textures"]["diffuse::Packed-one.png"] = _flat_png_uri(
        (80, 80, 80, 255))
    low_mask_key = "normal_data::Packed-body-no-toon-mask.png"
    body_payload["textures"][low_mask_key] = _flat_png_uri((128, 128, 0, 0))
    entry["normal_data_key"] = low_mask_key
    endpoint_light_key = entry["light_map_key"]
    body_payload["textures"][endpoint_light_key] = _flat_png_uri(
        (255, 0, 0, 255))

    base_payload = copy.deepcopy(body_payload)
    base_entry = base_payload["meshes"]["Body-Packed-0"]
    base_entry["material_profile_id"] = "wuwa:rabbitfx"
    base_entry["light_map_key"] = None
    base_profile = material_profile_for("wuwa", "rabbitfx").to_metadata()
    base_payload["metadata"]["material_profiles"] = {
        base_profile["id"]: base_profile,
    }

    def render(payload):
        context, page = _page(edge_browser, frontend_url, {"Packed": payload})
        try:
            _open(page, "Packed")
            page.wait_for_function(
                "window.modViewer.activeMeshes[0]?.material?.userData"
                "?.gameMaterial?.bindings.normal_data.enabledNode.value === true")
            page.evaluate("""
              async () => {
                const THREE = await import('three');
                const {scene, controls} = await import('./js/scene/scene.js');
                const {requestRender} = await import('./js/scene/render-scheduler.js');
                scene.traverse(object => {
                  if (object.isAmbientLight || object.isHemisphereLight) {
                    object.intensity = 0;
                  } else if (object.isSprite || object.isGridHelper) {
                    object.visible = false;
                  }
                });
                const key = scene.children.find(object => object.isDirectionalLight);
                key.target.position.copy(controls.target);
                key.position.copy(controls.target).add(new THREE.Vector3(0, 0, 3));
                key.intensity = 3;
                window.modViewer.activeMeshes[0].material.roughness = 0.2;
                requestRender();
              }
            """)
            page.wait_for_timeout(400)
            return _sample_mesh_pixel(page)
        finally:
            context.close()

    packed_pixel = render(body_payload)
    physical_pixel = render(base_payload)
    assert abs(sum(packed_pixel) - sum(physical_pixel)) <= 3, (
        packed_pixel, physical_pixel)

def test_wuwa_packed_rg_normal_matches_derived_reference_and_y_sign(
        edge_browser, frontend_url):
    # Keep the source constant so the comparison exercises normal decoding,
    # not texture filtering or mip selection.  These are the same channels
    # used to build the old CPU-derived RGB reference.
    red, green = 160, 192
    x = red / 127.5 - 1.0
    y = green / 127.5 - 1.0
    z = max(0.0, 1.0 - x * x - y * y) ** 0.5
    blue = round((z * 0.5 + 0.5) * 255.0)
    diffuse_uri = _flat_png_uri((120, 120, 120, 255))
    packed_uri = _flat_png_uri((red, green, 17, 241))
    derived_uri = _flat_png_uri((red, green, blue, 255))

    def configure_light(page):
        page.evaluate("""
          async () => {
                const THREE = await import('three');
                const {scene, controls} = await import('./js/scene/scene.js');
                const {requestRender} = await import('./js/scene/render-scheduler.js');
            let key = null;
            scene.traverse(object => {
              if (object.isAmbientLight || object.isHemisphereLight) {
                object.intensity = 0;
              } else if (object.isSprite || object.isGridHelper) {
                object.visible = false;
              } else if (object.isDirectionalLight) {
                if (!key) key = object;
                else object.intensity = 0;
              }
            });
            key.target.position.copy(controls.target);
            key.position.copy(controls.target)
              .add(new THREE.Vector3(0.8, 0.4, 2.0));
            key.intensity = 1;
            requestRender();
          }
        """)
        page.wait_for_timeout(400)

    def sample_quad(page):
        return [
            _sample_mesh_pixel_at(page, x, y)
            for x, y in ((0.15, 0.15), (0.5, 0.15), (0.85, 0.15),
                         (0.15, 0.85), (0.85, 0.85))
        ]

    reference = _parity_payload(diffuse_uri)
    reference_entry = reference["meshes"]["Body-Parity-0"]
    reference_key = "normal_map::Parity-reference.png"
    reference_entry["normal_map_key"] = reference_key
    reference["textures"][reference_key] = derived_uri

    reference_context, reference_page = _page(
        edge_browser, frontend_url, {"Reference": reference})
    packed_context = None
    try:
        _open(reference_page, "Reference")
        reference_page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData"
            "?.gameMaterial?.bindings.normal_map.textureNode.value.image"
            "?.width === 4")
        configure_light(reference_page)
        reference_pixels = sample_quad(reference_page)

        packed = _packed_material_payload("wuwa:raw")
        packed_entry = packed["meshes"]["Body-Packed-0"]
        packed_entry["drawindexed"] = [6, 0, 0]
        packed_entry["pos"] = _f32(
            -1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0)
        packed_entry["uv"] = _f32(0, 0, 1, 0, 1, 1, 0, 1)
        packed_entry["idx"] = _u32(0, 1, 2, 0, 2, 3)
        packed_entry["normal_data_key"] = "normal_data::Packed-reference.png"
        packed["textures"] = {
            "diffuse::Packed-one.png": diffuse_uri,
            packed_entry["normal_data_key"]: packed_uri,
        }
        packed_context, packed_page = _page(
            edge_browser, frontend_url, {"Packed": packed})
        _open(packed_page, "Packed")
        packed_page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData"
            "?.gameMaterial?.bindings.normal_data.textureNode.value.image"
            "?.width === 4")
        configure_light(packed_page)
        packed_pixels = sample_quad(packed_page)

        assert all(
            max(abs(a - b) for a, b in zip(reference_pixel, packed_pixel)) <= 8
            for reference_pixel, packed_pixel
            in zip(reference_pixels, packed_pixels)
        ), (reference_pixels, packed_pixels, (red, green, blue))

        packed_page.evaluate("""
          async () => {
            const {setMeshTextureState} = await import('./js/mesh/mesh-factory.js');
            const mesh = window.modViewer.activeMeshes[0];
            setMeshTextureState(mesh, {
              diffuse: mesh.userData.texKey,
              normal_map: null,
              normal_data: null,
              light_map: null,
              material_map: null,
            });
          }
        """)
        packed_page.wait_for_timeout(300)
        geometry_pixels = sample_quad(packed_page)
        assert any(
            sum(abs(a - b) for a, b in zip(packed_pixel, geometry_pixel)) > 6
            for packed_pixel, geometry_pixel
            in zip(packed_pixels, geometry_pixels)
        ), (packed_pixels, geometry_pixels)

        packed_page.evaluate("""
          async key => {
            const {setMeshTextureState} = await import('./js/mesh/mesh-factory.js');
            const mesh = window.modViewer.activeMeshes[0];
            mesh.userData.normalMapYSign = 1;
            setMeshTextureState(mesh, {
              diffuse: mesh.userData.texKey,
              normal_map: null,
              normal_data: key,
              light_map: null,
              material_map: null,
            });
          }
        """, packed_entry["normal_data_key"])
        packed_page.wait_for_timeout(300)
        positive_y_pixels = sample_quad(packed_page)
        assert any(
            sum(abs(a - b) for a, b in zip(packed_pixel, positive_pixel)) > 6
            for packed_pixel, positive_pixel
            in zip(packed_pixels, positive_y_pixels)
        ), (packed_pixels, positive_y_pixels)
    finally:
        reference_context.close()
        if packed_context is not None:
            packed_context.close()

def test_wuwa_missing_lightmap_disables_shadow_mask_without_rebuilding(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx")
    entry = payload["meshes"]["Body-Packed-0"]
    low_key = "light_map::Packed-shadow-low.png"
    payload["textures"] = {
        key: _flat_png_uri((255, 255, 255, 255))
        for key in payload["textures"]
    }
    payload["textures"]["diffuse::Packed-one.png"] = _flat_png_uri(
        (24, 24, 24, 255))
    payload["textures"][entry["normal_data_key"]] = _flat_png_uri(
        (128, 128, 255, 255))
    # Keep the low value below the shadow cutoff but above the invalid
    # endpoint tolerance; an exact zero is intentionally treated as absent.
    payload["textures"][low_key] = _flat_png_uri((0, 16, 0, 255))
    entry["light_map_key"] = low_key

    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial"
            "?.bindings.light_map.enabledNode.value === true")
        page.wait_for_function("""
          () => {
            const game = window.modViewer.activeMeshes[0]?.material
              ?.userData?.gameMaterial;
            return game?.bindings?.diffuse?.enabledNode?.value === true
              && game.bindings.normal_data.enabledNode.value === true
              && game.bindings.light_map.enabledNode.value === true;
          }
        """)
        page.evaluate("""
          async () => {
            const THREE = await import('three');
            const {scene, controls} = await import('./js/scene/scene.js');
            const {requestRender} = await import('./js/scene/render-scheduler.js');
            scene.traverse(object => {
              if (object.isAmbientLight || object.isHemisphereLight) {
                object.intensity = 0;
              } else if (object.isSprite || object.isGridHelper) {
                object.visible = false;
              }
            });
            const key = scene.children.find(object => object.isDirectionalLight);
            key.target.position.copy(controls.target);
            key.position.copy(controls.target).add(new THREE.Vector3(0.8, 0, 0.35));
            key.intensity = 1;
            requestRender();
          }
        """)
        page.wait_for_timeout(400)
        page.locator("#toon-btn").click()
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.toonEnabledNode?.value === true
        """)
        page.wait_for_timeout(250)
        shadowed_pixel = _sample_mesh_pixel(page)
        before_version = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          window.__missingLightmapMaterial = mesh.material;
          return mesh.material.version;
        }""")
        page.evaluate("""async () => {
          const {setMeshTextureState} = await import('./js/mesh/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          setMeshTextureState(mesh, {
            diffuse: mesh.userData.texKey,
            normal_map: null,
            normal_data: mesh.userData.normalDataKey,
            light_map: null,
            material_map: null,
          });
        }""")
        page.wait_for_function("""
          () => window.modViewer.activeMeshes[0]?.material?.userData
            ?.gameMaterial?.bindings?.light_map?.enabledNode?.value === false
        """)
        state = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          return {
            bound: mesh.material.userData.gameMaterial
              .bindings.light_map.enabledNode.value,
            sameMaterial: mesh.material === window.__missingLightmapMaterial,
            version: mesh.material.version,
          };
        }""")
        missing_pixel = _sample_mesh_pixel(page)
        assert state["bound"] is False
        assert state["version"] == before_version
        assert state["sameMaterial"]
        assert sum(missing_pixel) > sum(shadowed_pixel), (
            shadowed_pixel, missing_pixel)
    finally:
        context.close()

def test_texture_rows_are_reused_for_control_changes_and_rebuilt_for_pool_changes(
        edge_browser, frontend_url):
    pick = {
        "tex_key": "diffuse::added.png", "file": "added.png", "uri":
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLkWQAAAABJRU5ErkJggg==",
    }
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")}, picks=[pick])
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        page.locator("#inspector-tab").click()
        page.locator(".group-hdr .group-name").first.click()
        page.locator(".draw-item").first.click()
        page.evaluate("""() => {
          window.__meshStateEvents = 0;
          window.addEventListener('mod-viewer-mesh-state-changed', () => {
            window.__meshStateEvents += 1;
          });
        }""")
        page.locator(".inspector-texture-option", has_text="A two").click()
        assert page.evaluate("window.__meshStateEvents") == 1
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].userData.manualTexOverride"
            " === 'diffuse::A-two.png'")
        page.evaluate("window.__textureRows = [...document.querySelectorAll('.inspector-texture-option')]")

        page.locator("#controls-tab").click()
        page.locator("#toggle-list .toggle-cycle-btn").click()
        assert page.evaluate("window.__textureRows.every((row, i) => row === document.querySelectorAll('.inspector-texture-option')[i])")
        page.locator("#menu-list .toggle-cycle-btn").click()
        assert page.evaluate("window.__textureRows.every((row, i) => row === document.querySelectorAll('.inspector-texture-option')[i])")
        page.locator("#menu-list .menu-slider").evaluate(
            "input => { input.value = '0.5'; input.dispatchEvent(new Event('input', {bubbles: true})); }")
        assert page.evaluate("window.__textureRows.every((row, i) => row === document.querySelectorAll('.inspector-texture-option')[i])")

        page.locator("#inspector-tab").click()
        page.locator(".inspector-manage-textures").click()
        page.locator("#texm-add").click()
        page.wait_for_function("document.querySelectorAll('.inspector-texture-option').length === 5")
        assert not page.evaluate("window.__textureRows[0] === document.querySelector('.inspector-texture-option')")
        page.evaluate("window.__textureRows = [...document.querySelectorAll('.inspector-texture-option')]")
        page.locator(".texm-row .toggle-icon-btn").last.click()
        page.wait_for_function("document.querySelectorAll('.inspector-texture-option').length === 4")
        assert not page.evaluate("window.__textureRows[0] === document.querySelector('.inspector-texture-option')")
    finally:
        context.close()

def test_authored_normals_survive_render_modes_and_neutral_shape(
        edge_browser, frontend_url):
    payload = _payload("Normals")
    entry = payload["meshes"]["Body-Normals-0"]
    entry["normal"] = _f32(1, 0, 0, 0, 1, 0, 0, 0, 1)
    entry["shape_targets"] = [{
        "var": "shape",
        "pos": _f32(0, 0, 0, 1, 0, 1, 0, 1, 0),
    }]
    context, page = _page(edge_browser, frontend_url, {"Normals": payload})
    try:
        _open(page, "Normals")
        page.locator(".draw-item").wait_for()
        initial = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          return {
            normals: [...mesh.geometry.attributes.normal.array],
            authored: mesh.userData.hasAuthoredNormals,
            base: [...mesh.userData.baseNormals],
            flat: mesh.material.flatShading,
          };
        }""")
        assert initial["authored"]
        assert initial["normals"] == initial["base"]
        assert not initial["flat"]

        page.locator("#shading-btn").click()
        shaded = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          return {
            normals: [...mesh.geometry.attributes.normal.array],
            flat: mesh.material.flatShading,
          };
        }""")
        assert shaded["flat"]
        assert shaded["normals"] == initial["normals"]

        page.evaluate("""async () => {
          const {setControlValue} = await import('./js/editing/control-state.js');
          const {refreshMeshes} = await import('./js/mesh/mesh-state.js');
          setControlValue('shape', '1');
          refreshMeshes();
        }""")
        deformed = page.evaluate(
            "() => [...window.modViewer.activeMeshes[0].geometry.attributes.normal.array]")
        assert deformed != initial["normals"]

        page.evaluate("""async () => {
          const {setControlValue} = await import('./js/editing/control-state.js');
          const {refreshMeshes} = await import('./js/mesh/mesh-state.js');
          setControlValue('shape', '0');
          refreshMeshes();
        }""")
        restored = page.evaluate(
            "() => [...window.modViewer.activeMeshes[0].geometry.attributes.normal.array]")
        assert restored == initial["normals"]
    finally:
        context.close()

def test_glossy_tool_applies_to_all_mesh_materials(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        assert page.locator("#glossy-btn").get_attribute("class") == "tool-btn off"
        assert page.locator("#glossy-btn").get_attribute("aria-pressed") == "false"
        before = page.evaluate("""
          () => window.modViewer.activeMeshes.map(mesh => mesh.material.roughness)
        """)
        assert before == [1]

        page.locator("#glossy-btn").click()
        glossy = page.evaluate("""
          () => window.modViewer.activeMeshes.map(mesh => mesh.material.roughness)
        """)
        assert glossy == [0.2]
        assert page.locator("#glossy-btn").get_attribute("aria-label") == (
            "Glossy materials: on")
        assert "off" not in (page.locator("#glossy-btn").get_attribute("class") or "")
        assert page.locator("#glossy-btn").get_attribute("aria-pressed") == "true"

        page.locator("#glossy-btn").click()
        restored = page.evaluate("""
          () => window.modViewer.activeMeshes.map(mesh => mesh.material.roughness)
        """)
        assert restored == [1]
        assert "off" in (page.locator("#glossy-btn").get_attribute("class") or "")
        assert page.locator("#glossy-btn").get_attribute("aria-pressed") == "false"
    finally:
        context.close()

def test_outline_child_shares_geometry_and_render_modes_suppress_it(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        structure = page.evaluate("""async () => {
          const THREE = await import('three');
          const mesh = window.modViewer.activeMeshes[0];
          const outline = mesh.children.filter(
            child => child.userData.isViewerOutline);
          const item = outline[0];
          return {
            count: outline.length,
            sharedGeometry: item?.geometry === mesh.geometry,
            isMeshBasicNodeMaterial: item?.material?.isMeshBasicNodeMaterial === true,
            hasVertexNode: item?.material?.vertexNode?.isNode === true,
            side: item?.material?.side,
            backSide: THREE.BackSide,
            depthTest: item?.material?.depthTest,
            depthWrite: item?.material?.depthWrite,
            renderOrder: item?.renderOrder,
            baseRenderOrder: mesh.renderOrder,
            map: item?.material?.map || null,
            state: window.modViewer.getOutlineState(0),
          };
        }""")
        assert structure["count"] == 1
        assert structure["sharedGeometry"]
        assert structure["isMeshBasicNodeMaterial"]
        assert structure["hasVertexNode"]
        assert structure["side"] == structure["backSide"]
        assert structure["depthTest"] is True
        assert structure["depthWrite"] is False
        assert structure["renderOrder"] > structure["baseRenderOrder"]
        assert structure["map"] is None
        assert structure["state"]["referenceProjectionSpan"] > 0
        base_outline_state = {
            "attached": True, "visible": False, "globalEnabled": False,
            "referenceWidthPixels": 0.75,
            "minWidthPixels": 0.5,
            "maxWidthPixels": 1.5,
            "effectiveWidthPixels": 0.75,
            "scaleMode": "view-depth-adaptive",
            "scalePerDepth": pytest.approx(
                2 * math.tan(math.radians(45 / 2)) * 0.75
                / structure["state"]["viewportHeight"]),
            "viewportHeight": structure["state"]["viewportHeight"],
            "effectiveFov": 45,
            "projectionSpan": structure["state"]["projectionSpan"],
            "referenceProjectionSpan": structure["state"][
                "referenceProjectionSpan"],
            "projectionRatio": pytest.approx(1),
            "suppressedByWireframe": False, "suppressedByDebug": False,
        }
        assert structure["state"]["projectionSpan"] == pytest.approx(
            structure["state"]["referenceProjectionSpan"])
        assert structure["state"] == base_outline_state

        page.locator("#outline-btn").click()
        assert page.evaluate("window.modViewer.getOutlineState(0)") == {
            **base_outline_state, "visible": True, "globalEnabled": True,
        }
        page.locator("#wire-btn").click()
        assert page.evaluate("window.modViewer.getOutlineState(0)") == {
            **base_outline_state, "globalEnabled": True,
            "suppressedByWireframe": True,
        }
        page.locator("#wire-btn").click()
        page.evaluate("window.modViewer.setMaterialDebugMode('shadow-mask')")
        assert page.evaluate("window.modViewer.getOutlineState(0)") == {
            **base_outline_state, "globalEnabled": True,
            "suppressedByDebug": True,
        }
        page.evaluate("window.modViewer.setMaterialDebugMode('off')")
        assert page.evaluate("window.modViewer.getOutlineState(0).visible")

        page.evaluate("window.modViewer.reloadCurrentMod()")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        assert page.evaluate("window.modViewer.getOutlineState(0)") == {
            **base_outline_state, "visible": True, "globalEnabled": True,
        }
    finally:
        context.close()


def test_outline_width_is_perspective_correct_and_material_stable(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"Outline": _outline_payload()})
    try:
        _open(page, "Outline")
        page.wait_for_function("window.modViewer.activeMeshes.length === 2")
        page.evaluate("""async () => {
          const {camera, controls, scene} = await import('./js/scene/scene.js');
          const {resetOutlineProjectionReference} =
            await import('./js/scene/outline-renderer.js');
          const {requestRender} = await import('./js/scene/render-scheduler.js');
          scene.traverse(object => {
            if (object.isGridHelper || object.isSprite) object.visible = false;
          });
          for (const mesh of window.modViewer.activeMeshes) {
            mesh.material.color.setHex(0xeeeeee);
          }
          camera.up.set(0, 1, 0);
          camera.zoom = 1;
          camera.fov = 45;
          camera.position.set(0, 0, 8);
          controls.target.set(0, 0, 0);
          camera.updateProjectionMatrix();
          camera.lookAt(controls.target);
          camera.updateMatrixWorld();
          controls.update();
          resetOutlineProjectionReference(camera, controls.target);
          const outlines = window.modViewer.activeMeshes.map(
            mesh => mesh.userData.viewerOutline);
          window.__outlineRefs = {
            outlines,
            materials: outlines.map(outline => outline.material),
            versions: outlines.map(outline => outline.material.version),
          };
          requestRender();
        }""")

        def configure(*, positions, scales, camera_z=8, fov=45,
                      camera_zoom=1,
                      visible=(True, True)):
            before = page.evaluate("window.modViewer.getRenderCount()")
            page.evaluate("""async state => {
              const {camera, controls} = await import('./js/scene/scene.js');
              const {requestRender} = await import('./js/scene/render-scheduler.js');
              window.modViewer.activeMeshes.forEach((mesh, index) => {
                mesh.position.fromArray(state.positions[index]);
                mesh.scale.setScalar(state.scales[index]);
                mesh.visible = state.visible[index];
                mesh.updateMatrix();
              });
              camera.position.set(0, 0, state.cameraZ);
              camera.fov = state.fov;
              camera.zoom = state.cameraZoom;
              controls.target.set(0, 0, 0);
              camera.updateProjectionMatrix();
              camera.lookAt(controls.target);
              camera.updateMatrixWorld();
              controls.update();
              requestRender();
            }""", {
                "positions": positions, "scales": scales,
                "cameraZ": camera_z, "fov": fov,
                "cameraZoom": camera_zoom,
                "visible": visible,
            })
            page.wait_for_function(
                "before => window.modViewer.getRenderCount() > before",
                arg=before)

        configure(
            positions=((-1.5, 0, 2), (1.5, 0, -2)),
            scales=(0.85, 0.85))
        depth_widths = _outline_widths(page)

        configure(
            positions=((-1.5, 0, 0), (1.5, 0, 0)),
            scales=(0.5, 1.8))
        scale_widths = _outline_widths(page)

        distance_widths = []
        distance_states = []
        distance_cases = (
            (2, 1.5),
            (4, 0.75 * math.sqrt(2)),
            (8, 0.75),
            (16, 0.75 * math.sqrt(0.5)),
            (32, 0.5),
            (48, 0.5),
        )
        for camera_z, expected_width in distance_cases:
            configure(
                positions=((0, 0, 0), (0, 0, 0)), scales=(1, 1),
                camera_z=camera_z, visible=(True, False))
            distance_widths.extend(_outline_widths(page))
            state = page.evaluate("window.modViewer.getOutlineState(0)")
            assert state["effectiveWidthPixels"] == pytest.approx(expected_width)
            distance_states.append(state)

        fov_widths = []
        fov_states = []
        for fov in (30, 60):
            configure(
                positions=((0, 0, 0), (0, 0, 0)), scales=(1, 1),
                fov=fov, visible=(True, False))
            fov_widths.extend(_outline_widths(page))
            fov_states.append(page.evaluate(
                "window.modViewer.getOutlineState(0)"))

        zoom_widths = []
        zoom_states = []
        for camera_zoom in (0.75, 1.5):
            configure(
                positions=((0, 0, 0), (0, 0, 0)), scales=(1, 1),
                camera_zoom=camera_zoom, visible=(True, False))
            zoom_widths.extend(_outline_widths(page))
            zoom_states.append(page.evaluate(
                "window.modViewer.getOutlineState(0)"))

        initial_viewport_height = page.evaluate(
            "window.modViewer.getOutlineState(0).viewportHeight")
        page.set_viewport_size({"width": 900, "height": 600})
        configure(
            positions=((0, 0, 0), (0, 0, 0)), scales=(1, 1),
            visible=(True, False))
        resized_width = _outline_widths(page)[0]
        resized_state = page.evaluate("window.modViewer.getOutlineState(0)")

        before_turn = page.evaluate("window.modViewer.getRenderCount()")
        page.locator("#camera-flip-btn").click()
        page.wait_for_function(
            "before => window.modViewer.getRenderCount() > before", arg=before_turn)
        rotated_width = _outline_widths(page)[0]

        all_widths = (
            depth_widths + scale_widths + distance_widths + fov_widths
            + zoom_widths
            + [resized_width, rotated_width])
        assert all(1 <= width <= 3 for width in all_widths), all_widths
        assert max(depth_widths) - min(depth_widths) <= 1, depth_widths
        assert max(scale_widths) - min(scale_widths) <= 1, scale_widths
        assert [state["projectionRatio"] for state in distance_states] == (
            pytest.approx([4, 2, 1, 0.5, 0.25, 1 / 6]))
        for state in fov_states + zoom_states:
            expected_width = max(
                0.5,
                min(1.5, 0.75 * math.sqrt(state["projectionRatio"])))
            assert state["effectiveWidthPixels"] == pytest.approx(
                expected_width)
        resized_canvas_height = page.evaluate("""async () => {
          const {renderer} = await import('./js/scene/scene.js');
          return renderer.domElement.clientHeight;
        }""")
        assert resized_state["viewportHeight"] == resized_canvas_height
        assert resized_state["viewportHeight"] != initial_viewport_height
        assert resized_state["effectiveFov"] == 45
        assert resized_state["effectiveWidthPixels"] == pytest.approx(0.75)

        configure(
            positions=((0, 0, 0), (0, 0, 0)), scales=(1, 1),
            camera_z=16, visible=(True, False))
        assert page.evaluate(
            "window.modViewer.getOutlineState(0).effectiveWidthPixels") < 0.75
        before_reset = page.evaluate("window.modViewer.getRenderCount()")
        page.locator("#camera-reset-view-btn").click()
        page.wait_for_function(
            "before => window.modViewer.getRenderCount() > before", arg=before_reset)
        reset_state = page.evaluate("window.modViewer.getOutlineState(0)")
        assert reset_state["projectionRatio"] == pytest.approx(1)
        assert reset_state["effectiveWidthPixels"] == pytest.approx(0.75)

        stability = page.evaluate("""() => {
          const current = window.modViewer.activeMeshes.map(
            mesh => mesh.userData.viewerOutline);
          return {
            sameOutlines: current.every(
              (outline, index) => outline === window.__outlineRefs.outlines[index]),
            sameMaterials: current.every((outline, index) =>
              outline.material === window.__outlineRefs.materials[index]),
            sameVersions: current.every((outline, index) =>
              outline.material.version === window.__outlineRefs.versions[index]),
            sharedMaterial: current[0].material === current[1].material,
            sharedGeometry: current.every((outline, index) =>
              outline.geometry === window.modViewer.activeMeshes[index].geometry),
          };
        }""")
        assert stability == {
            "sameOutlines": True, "sameMaterials": True,
            "sameVersions": True, "sharedMaterial": True,
            "sharedGeometry": True,
        }

        settled_count = page.evaluate("window.modViewer.getRenderCount()")
        page.wait_for_timeout(250)
        assert page.evaluate("window.modViewer.getRenderCount()") == settled_count
    finally:
        context.close()


def test_wuwa_models_start_with_a_180_degree_base_turn(
        edge_browser, frontend_url):
    payload = _payload("WuWa")
    payload["metadata"]["game"] = {
        "id": "wuwa", "runtime": "wwmi", "texture_api": "raw",
        "confidence": "high",
    }
    context, page = _page(edge_browser, frontend_url, {"WuWa": payload})
    try:
        _open(page, "WuWa")
        page.locator(".draw-item").wait_for()

        initial = page.evaluate("""() => ({
          position: window.modViewer.activeMeshes[0].position.toArray(),
          quaternion: window.modViewer.activeMeshes[0].quaternion.toArray(),
        })""")
        assert initial["quaternion"] == pytest.approx([0, 1, 0, 0])

        page.locator("#camera-reset-view-btn").click()
        reset = page.evaluate("""() => ({
          position: window.modViewer.activeMeshes[0].position.toArray(),
          quaternion: window.modViewer.activeMeshes[0].quaternion.toArray(),
        })""")
        assert reset["position"] == pytest.approx(initial["position"])
        assert reset["quaternion"] == pytest.approx(initial["quaternion"])

        reloaded = page.evaluate("""async () => {
          await window.modViewer.reloadCurrentMod();
          return window.modViewer.activeMeshes[0].quaternion.toArray();
        }""")
        assert reloaded == pytest.approx([0, 1, 0, 0])
    finally:
        context.close()

def test_conditional_only_texture_survives_component_run_reconciliation(
        edge_browser, frontend_url):
    payload = _payload("ConditionalOnly")
    entry = payload["meshes"]["Body-ConditionalOnly-0"]
    entry["tex_key"] = None
    entry["texture_variants"] = [{
        "conditions": [[{
            "var": "menu", "value": "0", "negate": False,
        }]],
        "tex_key": "diffuse::ConditionalOnly-two.png",
    }]
    context, page = _page(
        edge_browser, frontend_url, {"ConditionalOnly": payload})
    try:
        _open(page, "ConditionalOnly")
        page.locator(".draw-item").wait_for()
        assert page.evaluate("window.modViewer.activeMeshes[0].userData.resolvedTexKey") == \
            "diffuse::ConditionalOnly-two.png"
        assert page.evaluate("window.modViewer.activeMeshes[0].userData.texKey") == \
            "diffuse::ConditionalOnly-two.png"
    finally:
        context.close()


def test_cached_model_bounds_do_not_rescan_positions(edge_browser, frontend_url):
    context = edge_browser.new_context(bypass_csp=True)
    page = context.new_page()
    try:
        page.goto(frontend_url)
        page.locator("#open-btn:not([disabled])").wait_for(timeout=10000)
        state = page.evaluate("""async () => {
          const THREE = await import('three');
          const {expandByModelMesh} = await import('./js/scene/model-bounds.js');
          const geometry = new THREE.BufferGeometry();
          const positions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
          geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
          const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
          const initial = new THREE.Box3();
          expandByModelMesh(initial, mesh);
          positions[0] = Infinity;
          const cached = new THREE.Box3();
          expandByModelMesh(cached, mesh);
          return {
            initialEmpty: initial.isEmpty(),
            cachedEmpty: cached.isEmpty(),
            boundsShared: cached.equals(initial),
          };
        }""")
        assert state == {
            "initialEmpty": False, "cachedEmpty": False, "boundsShared": True,
        }
    finally:
        context.close()

def test_character_shadows_are_on_demand_and_visibility_keeps_stable_ground(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"Shadow": _payload("Shadow")})
    try:
        _open(page, "Shadow")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("""async () => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          return getCharacterShadowDebugState().shadowUpdateCount > 0;
        }""")
        initial = page.evaluate("""async () => {
          const {getCharacterShadowDebugState, renderer, scene} =
            await import('./js/scene/scene.js');
          const ground = [];
          scene.traverse(object => { if (object.userData.isViewerGround) ground.push(object); });
          return {
            enabled: renderer.shadowMap.enabled,
            ground: ground.map(object => ({
              receive: object.receiveShadow, cast: object.castShadow,
              shadowMaterial: object.material.isShadowMaterial,
              y: object.position.y,
            })),
            debug: getCharacterShadowDebugState(),
          };
        }""")
        assert initial["enabled"]
        assert initial["ground"] == [{
            "receive": True, "cast": False, "shadowMaterial": True,
            "y": initial["ground"][0]["y"],
        }]
        assert initial["debug"]["modelBounds"] is not None
        assert initial["debug"]["casterBounds"] is not None

        updated = page.evaluate("""async () => {
          const {applyMeshVisibility} = await import('./js/mesh/mesh-state.js');
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          const mesh = window.modViewer.activeMeshes[0];
          mesh.userData.manualVisible = false;
          applyMeshVisibility(mesh);
          return getCharacterShadowDebugState();
        }""")
        page.wait_for_function(
            "previous => window.modViewer.getRenderCount() > previous",
            arg=page.evaluate("window.modViewer.getRenderCount()"))
        after = page.evaluate("""async () => {
          const {getCharacterShadowDebugState, scene} = await import('./js/scene/scene.js');
          let ground = null;
          scene.traverse(object => { if (object.userData.isViewerGround) ground = object; });
          return {debug: getCharacterShadowDebugState(), groundY: ground.position.y};
        }""")
        assert after["debug"]["fitCount"] > updated["fitCount"]
        assert after["debug"]["shadowUpdateCount"] > updated["shadowUpdateCount"]
        assert after["groundY"] == pytest.approx(initial["ground"][0]["y"])
    finally:
        context.close()


def test_wireframe_toggles_rim_uniform_without_rebuilding_material(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"Rim": _payload("Rim")})
    try:
        _open(page, "Rim")
        page.locator(".draw-item").wait_for()
        initial = page.evaluate("""() => {
          const material = window.modViewer.activeMeshes[0].material;
          window.__rimMaterial = material;
          const state = material.userData.gameMaterial;
          return {
            version: material.version,
            enabled: state.rimEnabledNode.value,
            strength: state.rimStrengthNode.value,
            power: state.rimPowerNode.value,
          };
        }""")
        assert initial["enabled"] is True
        assert initial["strength"] > 0
        assert initial["power"] > 0
        page.locator("#wire-btn").click()
        disabled = page.evaluate("""() => {
          const material = window.modViewer.activeMeshes[0].material;
          return {same: material === window.__rimMaterial, version: material.version,
            enabled: material.userData.gameMaterial.rimEnabledNode.value};
        }""")
        assert disabled == {"same": True, "version": initial["version"], "enabled": False}
        page.locator("#wire-btn").click()
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].material.userData.gameMaterial.rimEnabledNode.value")
    finally:
        context.close()


def test_key_light_mode_changes_restore_ground_without_refitting_shadows(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"LightMode": _payload("LightMode")})
    try:
        _open(page, "LightMode")
        page.locator(".draw-item").wait_for()
        page.wait_for_function("""async () => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          return getCharacterShadowDebugState().groundVisible;
        }""")
        before = page.evaluate("""async () => {
          const {getCharacterShadowDebugState, setLightMode} = await import('./js/scene/scene.js');
          setLightMode('off');
          return getCharacterShadowDebugState();
        }""")
        page.wait_for_function("""async () => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          return !getCharacterShadowDebugState().groundVisible;
        }""")
        off = page.evaluate("""async () => {
          const {getCharacterShadowDebugState, setLightMode} = await import('./js/scene/scene.js');
          setLightMode('current');
          return getCharacterShadowDebugState();
        }""")
        page.wait_for_function("""async () => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          return getCharacterShadowDebugState().groundVisible;
        }""")
        restored = page.evaluate("""async () => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          return getCharacterShadowDebugState();
        }""")
        assert off["fitCount"] == before["fitCount"]
        assert off["shadowUpdateCount"] == before["shadowUpdateCount"]
        assert restored["fitCount"] == before["fitCount"]
        assert restored["shadowUpdateCount"] == before["shadowUpdateCount"]
    finally:
        context.close()


@pytest.mark.parametrize("scale", [0.01, 1, 100])
def test_shadow_fit_and_bias_scale_with_model_size(edge_browser, frontend_url, scale):
    label = f"ShadowScale{scale}"
    payload = _payload(label)
    entry = payload["meshes"][f"Body-{label}-0"]
    entry["pos"] = _f32(0, 0, 0, scale, 0, 0, 0, scale, scale * 0.25)
    context, page = _page(edge_browser, frontend_url, {label: payload})
    try:
        _open(page, label)
        page.locator(".draw-item").wait_for()
        state = page.evaluate("""async () => {
          const {getCharacterShadowDebugState, scene} = await import('./js/scene/scene.js');
          const light = scene.children.find(object => object.isDirectionalLight);
          const camera = light.shadow.camera;
          return {
            debug: getCharacterShadowDebugState(),
            camera: [camera.left, camera.right, camera.bottom, camera.top,
              camera.near, camera.far],
          };
        }""")
        assert all(math.isfinite(value) for value in state["camera"])
        left, right, bottom, top, near, far = state["camera"]
        assert left < right and bottom < top and near < far
        bounds = state["debug"]["modelBounds"]
        diagonal = math.dist(bounds["min"], bounds["max"])
        assert state["debug"]["normalBias"] == pytest.approx(
            max(diagonal, 0.001) * 0.0015)
    finally:
        context.close()


def test_shape_and_view_changes_refit_character_shadows(edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"ShadowShape": _payload("ShadowShape")})
    try:
        _open(page, "ShadowShape")
        page.locator(".draw-item").wait_for()
        initial = page.evaluate("""async () => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          return getCharacterShadowDebugState().fitCount;
        }""")
        page.evaluate("""async () => {
          const {setControlValue} = await import('./js/editing/control-state.js');
          const {refreshMeshes} = await import('./js/mesh/mesh-state.js');
          setControlValue('shape', '1');
          refreshMeshes();
        }""")
        page.wait_for_function("""async count => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          return getCharacterShadowDebugState().fitCount > count;
        }""", arg=initial)
        after_shape = page.evaluate("""async () => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          return getCharacterShadowDebugState().fitCount;
        }""")
        for button_id in ("#camera-flip-btn", "#camera-flip-horizontal-btn", "#camera-reset-view-btn"):
            page.locator(button_id).click()
            page.wait_for_function("""async count => {
              const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
              return getCharacterShadowDebugState().fitCount > count;
            }""", arg=after_shape)
            after_shape = page.evaluate("""async () => {
              const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
              return getCharacterShadowDebugState().fitCount;
            }""")
    finally:
        context.close()


def test_camera_motion_with_ao_reuses_shadow_map_but_light_motion_updates_it(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"ShadowMotion": _payload("ShadowMotion")})
    try:
        _open(page, "ShadowMotion")
        page.locator(".draw-item").wait_for()
        _set_ao_level(page, 100)
        page.wait_for_function("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          const state = getViewportRenderPipelineDebugState();
          return state.effectiveStrength === 1 && state.aoRenderCount > 0;
        }""")
        page.wait_for_function("""async () => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          return getCharacterShadowDebugState().groundVisible;
        }""")
        baseline = page.evaluate("""async () => {
          const {
            getCharacterShadowDebugState,
            getViewportRenderPipelineDebugState,
          } = await import('./js/scene/scene.js');
          return {
            shadow: getCharacterShadowDebugState(),
            ao: getViewportRenderPipelineDebugState(),
          };
        }""")
        render_count = page.evaluate("window.modViewer.getRenderCount()")
        page.evaluate("""async () => {
          const {camera} = await import('./js/scene/scene.js');
          const {requestRender} = await import('./js/scene/render-scheduler.js');
          camera.position.x += 0.2;
          requestRender();
        }""")
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() > count", arg=render_count)
        after_camera = page.evaluate("""async () => {
          const {
            getCharacterShadowDebugState,
            getViewportRenderPipelineDebugState,
          } = await import('./js/scene/scene.js');
          return {
            shadow: getCharacterShadowDebugState(),
            ao: getViewportRenderPipelineDebugState(),
          };
        }""")
        assert after_camera["shadow"]["fitCount"] == baseline["shadow"]["fitCount"]
        assert after_camera["shadow"]["shadowUpdateCount"] == baseline["shadow"]["shadowUpdateCount"]
        assert after_camera["ao"]["aoRenderCount"] > baseline["ao"]["aoRenderCount"]

        page.evaluate("""async () => {
          const THREE = await import('three');
          const {scene} = await import('./js/scene/scene.js');
          const {requestRender} = await import('./js/scene/render-scheduler.js');
          scene.children.find(object => object.isDirectionalLight)
            .position.add(new THREE.Vector3(0.25, 0, 0));
          requestRender();
        }""")
        page.wait_for_function("""async state => {
          const {getCharacterShadowDebugState} = await import('./js/scene/scene.js');
          const next = getCharacterShadowDebugState();
          return next.fitCount > state.fitCount
            && next.shadowUpdateCount > state.shadowUpdateCount;
        }""", arg=after_camera["shadow"])
    finally:
        context.close()


def test_debug_output_bypasses_viewer_rim_lighting(edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx")
    context, page = _page(edge_browser, frontend_url, {"DebugRim": payload})
    try:
        _open(page, "DebugRim")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial")
        page.evaluate("""async () => {
          const {requestRender} = await import('./js/scene/render-scheduler.js');
          const game = window.modViewer.activeMeshes[0].material.userData.gameMaterial;
          game.rimStrengthNode.value = 0;
          window.modViewer.setMaterialDebugMode('normal-data-b');
          window.modViewer.setAmbientOcclusionStrength(0);
          requestRender();
        }""")
        page.wait_for_timeout(250)
        without_ao = _sample_mesh_pixel(page)
        before_ao = page.evaluate("""async () => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          return getViewportRenderPipelineDebugState();
        }""")
        page.evaluate("window.modViewer.setAmbientOcclusionStrength(1)")
        page.wait_for_function("""async state => {
          const {getViewportRenderPipelineDebugState} =
            await import('./js/scene/scene.js');
          const next = getViewportRenderPipelineDebugState();
          return next.effectiveStrength === 1
            && next.aoRenderCount > state.aoRenderCount;
        }""", arg=before_ao)
        page.wait_for_timeout(250)
        with_ao = _sample_mesh_pixel(page)
        assert with_ao == pytest.approx(without_ao, abs=2)

        page.evaluate("""async () => {
          const {requestRender} = await import('./js/scene/render-scheduler.js');
          window.modViewer.setAmbientOcclusionStrength(0);
          requestRender();
        }""")
        page.wait_for_timeout(250)
        without_rim = _sample_mesh_pixel(page)
        page.evaluate("""async () => {
          const {requestRender} = await import('./js/scene/render-scheduler.js');
          window.modViewer.activeMeshes[0].material.userData.gameMaterial
            .rimStrengthNode.value = 20;
          requestRender();
        }""")
        page.wait_for_timeout(250)
        with_rim = _sample_mesh_pixel(page)
        assert with_rim == pytest.approx(without_rim, abs=2)
    finally:
        context.close()
