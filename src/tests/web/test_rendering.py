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

def test_skinning_physics_solver_uses_true_3d_vectors_and_quaternions(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"Physics3D": _payload("Physics3D")})
    try:
        result = page.evaluate("""async () => {
          const physics = await import('./js/mesh/weight-physics.js');
          const deformation = await import('./js/mesh/weight-deformation.js');
          const THREE = await import('three');
          const forest = {
            components: [{
              componentId: 0, rootId: 0, nodeIds: [0, 1, 2, 3],
              maxDepth: 2, depthById: {0: 0, 1: 1, 2: 2, 3: 2},
              childrenById: {0: [1], 1: [2, 3]},
            }],
          };
          const initial = physics.initializePhysicsState(forest);
          const initialShape = [...initial.joints.values()].map(joint => ({
            rotationVector: joint.rotationVector,
            angularVelocity: joint.angularVelocity,
          }));
          const targets = physics.buildPhysicsTargetRotations(
            forest, [.4, .2, -.2]);
          const kicked = physics.initializePhysicsState(forest);
          physics.applyReferenceFrameAngularDelta(
            kicked, forest, [.3, .4, .5], 1);
          physics.applyPhysicsKick(kicked, forest, [1, 2, 3]);
          const spring = physics.initializePhysicsState(forest);
          const targetRotationByBoneId = new Map([
            [1, [.15, .1, -.05]], [2, [.15, .1, -.05]], [3, [.15, .1, -.05]],
          ]);
          for (let index = 0; index < 1800; index += 1) {
            physics.stepSpringPhysics(spring, forest, 1 / 120, {
              targetRotationByBoneId, frequencyHz: 2, dampingRatio: 1,
            });
          }
          const settled = physics.isPhysicsSettled(
            spring, forest, [0, 0, 0], {
              targetRotationByBoneId, rotationTolerance: .002,
              velocityTolerance: .002,
            });

          const centers = new Map([
            [0, [0, 0, 0]], [1, [1, 1, 0]],
            [2, [2, 1, 0]], [3, [1, 2, 0]],
          ]);
          const translated = physics.initializePhysicsState(forest);
          const translationDiagnostics = {};
          physics.applyReferenceFrameTranslationDelta(
            translated, forest, centers, [.2, .1, .3], 1,
            translationDiagnostics);
          const velocity = physics.initializePhysicsState(forest);
          const velocityDiagnostics = {};
          physics.applyReferenceFrameLinearVelocityDelta(
            velocity, forest, centers, [.2, .1, .3], 1,
            velocityDiagnostics);

          const gravityForest = {
            components: [{rootId: 0, nodeIds: [0, 1], maxDepth: 1,
              depthById: {0: 0, 1: 1}}],
          };
          const gravity = physics.buildGravityAngularAccelerations(
            gravityForest,
            new Map([[0, [0, 0, 0]], [1, [0, 1, 0]]]),
            [1, 0, 0], {referenceRadius: 1});
          const limits = new Map([[1, .5]]);
          const limited = physics.initializePhysicsState(gravityForest);
          limited.joints.get(1).rotationVector = [.4, .3, 0];
          limited.joints.get(1).angularVelocity = [.8, .6, 1];
          physics.applyPhysicsJointLimits(limited, limits);
          const zeroLimited = physics.initializePhysicsState(gravityForest);
          zeroLimited.joints.get(1).rotationVector = [.1, .2, .3];
          zeroLimited.joints.get(1).angularVelocity = [1, 2, 3];
          physics.applyPhysicsJointLimits(zeroLimited, new Map([[1, 0]]));

          const rotations = new Map([
            [1, [0, 0, Math.PI / 2]],
            [2, [Math.PI / 2, 0, 0]],
          ]);
          const transforms = deformation.buildForestTransformsFromLocalRotations(
            forest, centers, {rotationByBoneId: rotations});
          const opposite = physics.rotationVectorBetween([1, 1, 0], [-1, -1, 0]);
          return {
            initialShape,
            targets: [...targets.entries()],
            kicked: [...kicked.joints.values()],
            spring: [...spring.joints.values()],
            settled,
            translated: [...translated.joints.values()]
              .map(joint => joint.rotationVector),
            translationDiagnostics,
            velocity: [...velocity.joints.values()]
              .map(joint => joint.angularVelocity),
            velocityDiagnostics,
            gravity: gravity.accelerationByBoneId.get(1),
            gravityDiagnostic: gravity.diagnostics.components[0],
            limited: limited.joints.get(1),
            zeroLimited: zeroLimited.joints.get(1),
            opposite,
            rootIdentity: transforms.get(0).equals(new THREE.Matrix4()),
            childMatrix: transforms.get(1).elements,
            grandchildMatrix: transforms.get(2).elements,
          };
        }""")
        assert all(
            entry["rotationVector"] == [0, 0, 0]
            and entry["angularVelocity"] == [0, 0, 0]
            for entry in result["initialShape"])
        assert [entry[0] for entry in result["targets"]] == [1, 2, 3]
        assert result["targets"][0][1] == pytest.approx([.2, .1, -.1])
        assert result["targets"][1][1] == pytest.approx([.2, .1, -.1])
        assert result["kicked"][0]["rotationVector"] == pytest.approx(
            [-.15, -.2, -.25])
        assert result["kicked"][1]["angularVelocity"] == pytest.approx(
            [1, 2, 3])
        assert result["spring"][0]["rotationVector"] == pytest.approx(
            [.15, .1, -.05], abs=.002)
        assert result["settled"]
        assert any(
            abs(value) > 1e-4
            for value in result["translated"][0])
        assert all(abs(value) > 1e-4 for value in result["velocity"][0])
        assert result["gravity"] == pytest.approx([0, 0, -9.81])
        assert result["gravityDiagnostic"]["totalAngularAccelerationVector"] == pytest.approx(
            [0, 0, -9.81])
        limited = result["limited"]
        assert math.sqrt(sum(value * value for value in limited["rotationVector"])) == pytest.approx(.5)
        assert limited["angularVelocity"][2] == pytest.approx(1)
        assert limited["angularVelocity"][0] == pytest.approx(0)
        assert limited["angularVelocity"][1] == pytest.approx(0)
        assert result["zeroLimited"] == {
            "rotationVector": [0, 0, 0], "angularVelocity": [0, 0, 0]}
        assert math.sqrt(sum(value * value for value in result["opposite"])) == pytest.approx(
            math.pi)
        assert result["rootIdentity"]
        assert result["childMatrix"] != result["grandchildMatrix"]
    finally:
        context.close()


def test_skinning_physics_drag_controller_owns_only_rmb(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"PhysicsDrag": _payload("PhysicsDrag")})
    try:
        result = page.evaluate("""async () => {
          const THREE = await import('three');
          const controllerModule = await import(
            './js/scene/physics-drag-controller.js');
          const canvas = document.createElement('canvas');
          canvas.width = 100;
          canvas.height = 100;
          canvas.getBoundingClientRect = () => ({
            left: 0, top: 0, width: 100, height: 100,
          });
          document.body.appendChild(canvas);
          const camera = new THREE.PerspectiveCamera();
          const actions = [];
          const motions = [];
          const controls = {
            unsetMouseAction: button => actions.push(['unset', button]),
            setMouseAction: (action, button) =>
              actions.push(['set', action, button]),
          };
          const controller = controllerModule.createPhysicsDragController({
            canvas, camera, controls,
            onMotion: detail => motions.push(detail),
          });
          const timestamped = (type, init, timeStamp) => {
            const event = new PointerEvent(type, {
              bubbles: true, ...init,
            });
            Object.defineProperty(event, 'timeStamp', {value: timeStamp});
            return event;
          };
          const dispatch = event => canvas.dispatchEvent(event);
          controller.setEnabled(true);
          dispatch(timestamped('pointerdown', {
            pointerId: 3, button: 2, clientX: 30, clientY: 30,
          }, 2000));
          dispatch(timestamped('pointerup', {
            pointerId: 3, button: 2, clientX: 30, clientY: 30,
          }, 2010));
          const plainClickMotionCount = motions.length;
          dispatch(timestamped('pointerdown', {
            pointerId: 1, button: 0, clientX: 10, clientY: 10,
          }, 0));
          dispatch(timestamped('pointerdown', {
            pointerId: 2, button: 2, clientX: 10, clientY: 10,
          }, 0));
          dispatch(timestamped('pointermove', {
            pointerId: 2, buttons: 2, clientX: 11, clientY: 11,
          }, 10));
          dispatch(timestamped('pointermove', {
            pointerId: 2, buttons: 2, clientX: 20, clientY: 0,
          }, 30));
          dispatch(timestamped('pointerup', {
            pointerId: 2, button: 2, clientX: 20, clientY: 0,
          }, 1020));
          const activeMotions = motions.filter(motion => motion.active);
          const release = motions[motions.length - 1];
          controller.setEnabled(false);
          const disabledActions = actions.slice();
          controller.dispose();
          canvas.remove();
          return {
            actions, disabledActions, activeMotions, release,
            plainClickMotionCount,
            lmbActive: motions.some(motion => motion.source !== 'rmb-drag'),
          };
        }""")
        assert result["actions"][0] == ["unset", 2]
        assert result["disabledActions"][-1] == ["set", "PAN", 2]
        assert result["activeMotions"]
        assert result["plainClickMotionCount"] == 0
        velocity = result["activeMotions"][0]["normalizedLinearVelocityWorld"]
        assert math.hypot(velocity[0], velocity[1]) == pytest.approx(4)
        assert velocity[0] > 0 and velocity[1] > 0 and velocity[2] == 0
        assert result["release"]["active"] is False
        assert result["release"]["normalizedLinearVelocityWorld"] == [0, 0, 0]
        assert result["lmbActive"] is False
    finally:
        context.close()


def test_active_vertex_deformation_updates_positions_and_authored_normals(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"ActiveDeform": _payload("ActiveDeform")})
    try:
        result = page.evaluate("""async () => {
          const deformation = await import('./js/mesh/weight-deformation.js');
          const THREE = await import('three');
          const baselinePositions = new Float32Array([
            1, 0, 0, 2, 0, 0, 3, 0, 0,
          ]);
          const outputPositions = new Float32Array(baselinePositions);
          const baselineNormals = new Float32Array([
            1, 0, 0, 1, 0, 0, 1, 0, 0,
          ]);
          const outputNormals = new Float32Array(baselineNormals);
          const indices = new Uint32Array([1, 2, 1, 2, 2, 0]);
          const weights = new Float32Array([1, 0, .5, .5, 1, 0]);
          const matrix = new THREE.Matrix4().makeRotationZ(Math.PI / 2)
            .setPosition(5, 7, 0);
          const rotation = new THREE.Quaternion().setFromAxisAngle(
            new THREE.Vector3(0, 0, 1), Math.PI / 2);
          const active = new Uint32Array([0, 1]);
          const positionCount = deformation.applyWeightedTransformDeformationInto(
            outputPositions, baselinePositions, indices, weights, 2,
            new Map([[1, matrix]]), active);
          const normalCount = deformation.applyWeightedNormalDeformationInto(
            outputNormals, baselineNormals, indices, weights, 2,
            new Map([[1, rotation]]), active);
          return {
            positions: [...outputPositions], normals: [...outputNormals],
            positionCount, normalCount,
          };
        }""")
        assert result["positionCount"] == 2
        assert result["normalCount"] == 2
        assert result["positions"][:3] == pytest.approx([5, 8, 0])
        assert result["positions"][3:6] == pytest.approx([3.5, 4.5, 0])
        assert result["positions"][6:] == [3, 0, 0]
        assert result["normals"][:3] == pytest.approx([0, 1, 0], abs=1e-6)
        assert result["normals"][3:6] == pytest.approx(
            [math.sqrt(.5), math.sqrt(.5), 0], abs=1e-6)
        assert result["normals"][6:] == [1, 0, 0]
    finally:
        context.close()


def test_model_physics_session_owns_fixed_clock_and_generation(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"PhysicsSession": _payload("PhysicsSession")})
    try:
        result = page.evaluate("""async () => {
          const {createModelPhysicsSession} = await import(
            './js/mesh/model-physics-session.js');
          const callbacks = [];
          const canceled = [];
          const events = [];
          const session = createModelPhysicsSession({
            requestAnimationFrame: callback => {
              callbacks.push(callback);
              return callback;
            },
            cancelAnimationFrame: callback => {
              canceled.push(callback);
              const index = callbacks.indexOf(callback);
              if (index >= 0) callbacks.splice(index, 1);
            },
            onInputOwnershipChanged: value => events.push(['input', value]),
          });
          const mesh = {};
          let steps = 0;
          let motions = 0;
          let detached = 0;
          let settledUpdates = 0;
          const participant = {
            mesh,
            onSessionDetached: () => { detached += 1; },
            onModelMotion: () => { motions += 1; return true; },
            step: () => { steps += 1; },
            updateSettled: () => { settledUpdates += 1; },
            isSettled: () => false,
            isVisible: () => false,
          };
          const transform = {
            orientation: [0, 0, 0, 1], translation: [0, 0, 0],
          };
          const generation = session.enable(transform);
          session.attach(participant);
          const firstFrame = callbacks.shift();
          firstFrame(0);
          const fixedFrame = callbacks.shift();
          fixedFrame(1000);
          session.handleModelTransform({
            modelTransform: {
              orientation: [0, .2, 0, .98], translation: [.1, 0, 0],
            },
          });
          const active = session.getState();
          session.disable();
          return {
            generation, activeGeneration: active.generation,
            steps, settledUpdates, motions, participantCount: active.participantCount,
            detached, canceled: canceled.length,
            disabled: session.getState(),
            inputEvents: events,
          };
        }""")
        assert result["generation"] == 1
        assert result["activeGeneration"] == 1
        assert result["steps"] == 6
        assert result["settledUpdates"] == 1
        assert result["motions"] == 1
        assert result["participantCount"] == 1
        assert result["detached"] == 1
        assert result["canceled"] >= 1
        assert result["disabled"]["enabled"] is False
        assert result["disabled"]["participantCount"] == 0
        assert result["disabled"]["generation"] == 2
        assert result["inputEvents"] == [["input", True], ["input", False]]
    finally:
        context.close()


def test_model_physics_reset_updates_numeric_defaults_once_and_keeps_toggles(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"PhysicsReset": _payload("PhysicsReset")})
    try:
        result = page.evaluate("""async () => {
          const physics = await import('./js/mesh/model-physics-session.js');
          let notifications = 0;
          let participantSettings = null;
          const session = physics.createModelPhysicsSession({
            onStateChanged: () => { notifications += 1; },
          });
          const transform = {
            orientation: [0, 0, 0, 1], translation: [0, 0, 0],
          };
          session.enable(transform);
          session.attach({
            mesh: {}, reset: settings => { participantSettings = {...settings}; },
            isSettled: () => true,
          });
          session.setSettings({
            frequencyHz: 7, dampingRatio: 1.2, angularResponse: .9,
            translationResponse: .8, velocityResponse: .7,
            gravityEnabled: true, gravityScale: 1.8,
            constraintsEnabled: true, maxBendDegrees: 12,
          });
          const before = notifications;
          const defaults = physics.DEFAULT_MODEL_PHYSICS_SETTINGS;
          const settingsPatch = {
            frequencyHz: defaults.frequencyHz,
            dampingRatio: defaults.dampingRatio,
            angularResponse: defaults.angularResponse,
            translationResponse: defaults.translationResponse,
            velocityResponse: defaults.velocityResponse,
            gravityScale: defaults.gravityScale,
            maxBendDegrees: defaults.maxBendDegrees,
          };
          session.reset(transform, {settingsPatch});
          const state = session.getState();
          const activeNotificationCount = notifications - before;
          session.disable();
          session.setSettings({frequencyHz: 7, dampingRatio: 1.2});
          const beforeDisabledReset = notifications;
          session.reset(transform, {settingsPatch});
          const disabledResetState = session.getState();
          const disabledResetNotifications = notifications - beforeDisabledReset;
          session.setSettings({frequencyHz: 7, gravityEnabled: true});
          session.destroy();
          return {
            notificationCount: activeNotificationCount,
            state, participantSettings, defaults,
            disabledResetNotifications, disabledResetState,
            destroyedState: session.getState(),
          };
        }""")
        assert result["notificationCount"] == 1
        assert result["state"]["gravityEnabled"] is True
        assert result["state"]["constraintsEnabled"] is True
        assert result["disabledResetNotifications"] == 1
        assert result["disabledResetState"]["enabled"] is False
        assert result["disabledResetState"]["frequencyHz"] == 2
        assert result["disabledResetState"]["dampingRatio"] == pytest.approx(.35)
        assert result["destroyedState"]["frequencyHz"] == 2
        assert result["destroyedState"]["gravityEnabled"] is False
        for key in (
                "frequencyHz", "dampingRatio", "angularResponse",
                "translationResponse", "velocityResponse", "gravityScale",
                "maxBendDegrees"):
            assert result["state"][key] == result["defaults"][key]
            assert result["participantSettings"][key] == result["defaults"][key]
    finally:
        context.close()


def test_physics_drag_preserves_arcball_camera_and_lmb_control(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"ArcballPhysics": _payload("ArcballPhysics")})
    try:
        _open(page, "ArcballPhysics")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        page.evaluate("""async () => {
          const mesh = window.modViewer.activeMeshes[0];
          const bytes = new Uint8Array(48);
          new Uint32Array(bytes.buffer).set([0, 1, 1, 2, 0, 2]);
          new Float32Array(bytes.buffer, 24).set([.8, .2, .7, .3, .6, .4]);
          const url = URL.createObjectURL(new Blob([bytes]));
          window.__arcballPhysicsUrl = url;
          window.pywebview.api.get_skinning_preview = async () => ({
            status: 'ok', vertex_count: 3, influence_count: 2,
            bone_ids: [0, 1, 2], encoding: 'test', source: {
              key: 'test/bodyblend.buf|offset=0', file: 'Test/BodyBlend.buf',
              bone_id_offset: 0,
            },
            data: {
              url, length: 48,
              indices: {offset: 0, length: 24, type: 'u32'},
              weights: {offset: 24, length: 24, type: 'f32'},
            }, diagnostics: {},
          });
          const experiment = await import('./js/mesh/weight-experiment.js');
          await experiment.loadSkinningWeights(mesh);
          experiment.setSelectedBones([{
            sourceKey: 'test/bodyblend.buf|offset=0',
            sourceFile: 'Test/BodyBlend.buf', boneIdOffset: 0, boneIds: [1],
          }]);
          await experiment.enableModelPhysics();
          return experiment.getModelPhysicsState();
        }""")
        canvas = page.locator("#canvas-container canvas").bounding_box()

        def view_state():
            return page.evaluate("""async () => {
              const {camera, controls} = await import('./js/scene/scene.js');
              return {
                camera: camera.position.toArray(),
                target: controls.target.toArray(),
              };
            }""")

        before_rmb = view_state()
        x = canvas["x"] + canvas["width"] * 0.5
        y = canvas["y"] + canvas["height"] * 0.5
        page.mouse.move(x, y)
        page.mouse.down(button="right")
        page.mouse.move(x + 70, y + 20, steps=4)
        page.mouse.up(button="right")
        page.wait_for_timeout(50)
        after_rmb = view_state()
        assert after_rmb["camera"] == pytest.approx(before_rmb["camera"])
        assert after_rmb["target"] == pytest.approx(before_rmb["target"])

        page.mouse.move(x, y)
        page.mouse.down(button="left")
        page.mouse.move(x + 45, y + 25, steps=4)
        page.mouse.up(button="left")
        page.wait_for_timeout(50)
        after_lmb = view_state()
        assert after_lmb != after_rmb

        page.evaluate("""async () => {
          const experiment = await import('./js/mesh/weight-experiment.js');
          experiment.disableModelPhysics();
        }""")
        before_pan = view_state()
        page.mouse.move(x, y)
        page.mouse.down(button="right")
        page.mouse.move(x + 40, y + 10, steps=4)
        page.mouse.up(button="right")
        page.wait_for_timeout(50)
        after_pan = view_state()
        assert after_pan != before_pan
    finally:
        page.evaluate("""() => {
          if (window.__arcballPhysicsUrl) {
            URL.revokeObjectURL(window.__arcballPhysicsUrl);
            window.__arcballPhysicsUrl = null;
          }
        }""")
        context.close()


def test_weight_load_rejects_missing_source_identity(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"WeightSourceRequired": _payload("WeightSourceRequired")})
    try:
        _open(page, "WeightSourceRequired")
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
          let error = null;
          try { await experiment.loadSkinningWeights(mesh); }
          catch (failure) { error = failure.message; }
          URL.revokeObjectURL(url);
          return {
            error,
            state: experiment.getSkinningState(mesh),
            model: experiment.getModelWeightState(),
          };
        }""")
        assert result["error"] == \
            "The skin-weight source identity is unavailable for this draw."
        assert result["state"]["loaded"] is False
        assert result["model"]["sources"] == []
        assert "legacy/model/weights.buf" not in str(result)
    finally:
        context.close()


def test_selected_weight_mask_aggregates_authored_influences(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"Weights": _payload("Weights")})
    try:
        result = page.evaluate("""async () => {
          const selection = await import('./js/mesh/weight-selection.js');
          const mask = selection.buildSelectedWeightMask(
            new Uint32Array([0, 1, 2, 3, 4, 5]),
            new Float32Array([.2, .3, .5, .6, .1, .9]), 2, [1, 2]);
          return [...mask];
        }""")
        assert result == pytest.approx([.3, .5, 0])
    finally:
        context.close()


def test_weight_picker_sampling_uses_smooth_distance_falloff_and_exact_fallback(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"WeightSampling": _payload("WeightSampling")})
    try:
        result = page.evaluate("""async () => {
          const selection = await import('./js/mesh/weight-selection.js');
          const nearby = selection.sampleNearbyBoneWeights(
            new Float32Array([0, 0, 0, .5, 0, 0, 1, 0, 0]),
            new Uint32Array([1, 2, 1, 2, 1, 2]),
            new Float32Array([.8, .2, .4, .6, .1, .9]), 2,
            [0, 0, 0], 1);
          const exact = selection.interpolateTriangleBoneWeights(
            new Uint32Array([1, 2, 1, 2, 1, 2]),
            new Float32Array([.8, .2, .4, .6, .1, .9]), 2,
            [0, 1, 2], [0.25, 0.5, 0.25]);
          return {nearby, exact};
        }""")
        assert [entry["boneId"] for entry in result["nearby"]] == [1, 2]
        assert [entry["weight"] for entry in result["nearby"]] == pytest.approx(
            [.6666667, .3333333])
        assert [entry["boneId"] for entry in result["exact"]] == [2, 1]
        assert [entry["weight"] for entry in result["exact"]] == pytest.approx(
            [.575, .425])
    finally:
        context.close()


def test_skinning_physics_lifecycle_sleeps_and_resets_vectors(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url,
        {"PhysicsLifecycle3D": _payload("PhysicsLifecycle3D")})
    try:
        _open(page, "PhysicsLifecycle3D")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        result = page.evaluate("""async () => {
          const mesh = window.modViewer.activeMeshes[0];
          const bytes = new Uint8Array(48);
          new Uint32Array(bytes.buffer).set([0, 1, 1, 2, 0, 2]);
          new Float32Array(bytes.buffer, 24).set([.8, .2, .7, .3, .6, .4]);
          const url = URL.createObjectURL(new Blob([bytes]));
          window.pywebview.api.get_skinning_preview = async () => ({
            status: 'ok', vertex_count: 3, influence_count: 2,
            bone_ids: [0, 1, 2], encoding: 'test', source: {
              key: 'test/bodyblend.buf|offset=0', file: 'Test/BodyBlend.buf',
              bone_id_offset: 0,
            },
            data: {
              url, length: 48,
              indices: {offset: 0, length: 24, type: 'u32'},
              weights: {offset: 24, length: 24, type: 'f32'},
            }, diagnostics: {},
          });
          const experiment = await import('./js/mesh/weight-experiment.js');
          await experiment.loadSkinningWeights(mesh);
          experiment.setSelectedBones([{
            sourceKey: 'test/bodyblend.buf|offset=0',
            sourceFile: 'Test/BodyBlend.buf', boneIdOffset: 0, boneIds: [1],
          }]);
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
            enabled: enabled.physicsEnabled,
            jointShape: [...enabled.physicsState.joints.values()]
              .every(joint => Array.isArray(joint.rotationVector)
                && Array.isArray(joint.angularVelocity)),
            scheduled: experiment.isPhysicsScheduled(mesh),
          };
          experiment.resetWeightPhysicsPerformanceStats();
          runFrame(0);
          runFrame(16.7);
          const sleeping = experiment.getSkinningState(mesh);
          const framePerformance = experiment.getWeightPhysicsPerformanceStats();
          experiment.resetWeightPhysicsPerformanceStats();
          window.dispatchEvent(new CustomEvent(
            'mod-viewer-virtual-model-motion', {detail: {
              normalizedLinearVelocityWorld: [.3, .1, .2],
              active: true, source: 'rmb-drag',
            }}));
          runFrame(33.4);
          for (let index = 0; index < 10; index += 1) {
            runFrame(50.1 + index * 16.7);
          }
          const movingFramePerformance =
            experiment.getWeightPhysicsPerformanceStats();
          const beforeVirtual = [...sleeping.physicsState.joints.values()]
            .map(joint => [...joint.angularVelocity]);
          const moving = experiment.getSkinningState(mesh);
          const movingVelocity = [...moving.physicsState.joints.values()]
            .map(joint => [...joint.angularVelocity]);
          window.dispatchEvent(new CustomEvent(
            'mod-viewer-virtual-model-motion', {detail: {
              normalizedLinearVelocityWorld: [0, 0, 0],
              active: false, source: 'rmb-drag',
            }}));
          const released = experiment.getSkinningState(mesh);
          const virtualVelocity = released.physicsVirtualLinearVelocityLocal;
          experiment.resetPhysicsMotion(mesh);
          const reset = experiment.getSkinningState(mesh);
          window.requestAnimationFrame = originalRequestAnimationFrame;
          window.cancelAnimationFrame = originalCancelAnimationFrame;
          URL.revokeObjectURL(url);
          return {
            enabledState, framePerformance,
            movingFramePerformance,
            activeVertices: sleeping.physicsActiveVertices.length,
            sleeping: sleeping.physicsSettled,
            beforeVirtual, movingVelocity, virtualVelocity,
            reset: [...reset.physicsState.joints.values()],
            enabledAfterReset: reset.physicsEnabled,
            scheduledAfterReset: experiment.isPhysicsScheduled(mesh),
          };
        }""")
        assert result["enabledState"] == {
            "enabled": True, "jointShape": True, "scheduled": True}
        assert result["activeVertices"] == 2
        assert result["framePerformance"]["physicsDeformedVertexCount"] == 2
        assert result["framePerformance"]["physicsBoundsUpdateCount"] == 1
        assert result["framePerformance"]["physicsUiNotifyCount"] == 0
        assert result["movingFramePerformance"]["physicsFrameCount"] == 10
        assert result["movingFramePerformance"]["dynamicShadowUpdateCount"] == 10
        assert result["movingFramePerformance"]["shadowFitCount"] == 0
        assert result["movingFramePerformance"]["physicsBoundsUpdateCount"] == 0
        assert result["movingFramePerformance"]["sourceTransformBuildCount"] == 10
        assert result["sleeping"]
        assert any(
            any(abs(value) > 1e-6 for value in vector)
            for vector in result["movingVelocity"])
        assert result["virtualVelocity"] == [0, 0, 0]
        assert all(
            joint["rotationVector"] == [0, 0, 0]
            and joint["angularVelocity"] == [0, 0, 0]
            for joint in result["reset"])
        assert result["enabledAfterReset"]
        assert not result["scheduledAfterReset"]
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
            bone_ids: [0, 1, 2], encoding: 'test', source: {
              key: 'test/bodyblend.buf|offset=0', file: 'Test/BodyBlend.buf',
              bone_id_offset: 0,
            },
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


def test_loaded_skinning_rebaselines_after_shape_change(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"SkinningShape": _payload("SkinningShape")})
    try:
        _open(page, "SkinningShape")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        result = page.evaluate("""async () => {
          const mesh = window.modViewer.activeMeshes[0];
          const bytes = new Uint8Array(48);
          new Uint32Array(bytes.buffer).set([0, 1, 1, 2, 0, 2]);
          new Float32Array(bytes.buffer, 24).set([.8, .2, .7, .3, .6, .4]);
          const url = URL.createObjectURL(new Blob([bytes]));
          window.pywebview.api.get_skinning_preview = async () => ({
            status: 'ok', vertex_count: 3, influence_count: 2,
            bone_ids: [0, 1, 2], encoding: 'test', source: {
              key: 'test/bodyblend.buf|offset=0', file: 'Test/BodyBlend.buf',
              bone_id_offset: 0,
            },
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
          const before = [...mesh.geometry.attributes.position.array];
          setControlValue('shape', '1');
          refreshMeshes();
          const state = experiment.getSkinningState(mesh);
          const after = [...mesh.geometry.attributes.position.array];
          URL.revokeObjectURL(url);
          return {
            loaded: state.loaded,
            baseline: [...state.baselinePositions],
            before, after,
            graph: state.influenceGraph,
          };
        }""")
        assert result["loaded"]
        assert result["before"] != result["after"]
        assert result["baseline"] == pytest.approx(result["after"])
        assert result["graph"] is None
    finally:
        context.close()


def test_model_physics_loads_eligible_meshes_with_partial_failures(
        edge_browser, frontend_url):
    payload = _payload("PartialPhysics")
    second = copy.deepcopy(next(iter(payload["meshes"].values())))
    second["component"] = "Hair PartialPhysics"
    payload["meshes"]["Hair-PartialPhysics-0"] = second
    context, page = _page(
        edge_browser, frontend_url, {"PartialPhysics": payload})
    try:
        _open(page, "PartialPhysics")
        page.wait_for_function("window.modViewer.activeMeshes.length === 2")
        result = page.evaluate("""async () => {
          const bytes = new Uint8Array(48);
          new Uint32Array(bytes.buffer).set([0, 1, 1, 2, 0, 2]);
          new Float32Array(bytes.buffer, 24).set([.8, .2, .7, .3, .6, .4]);
          const url = URL.createObjectURL(new Blob([bytes]));
          window.pywebview.api.get_skinning_preview = async (
            _path, semanticKey) => {
            if (semanticKey.startsWith('Hair-')) {
              return {status: 'error', code: 'unsupported_skinning_layout'};
            }
            return {
              status: 'ok', vertex_count: 3, influence_count: 2,
              bone_ids: [0, 1, 2], encoding: 'test', source: {
                key: 'test/bodyblend.buf|offset=0', file: 'Test/BodyBlend.buf',
                bone_id_offset: 0,
              },
              data: {
                url, length: 48,
                indices: {offset: 0, length: 24, type: 'u32'},
                weights: {offset: 24, length: 24, type: 'f32'},
              }, diagnostics: {},
            };
          };
          const experiment = await import('./js/mesh/weight-experiment.js');
          for (const mesh of window.modViewer.activeMeshes) {
            try { await experiment.loadSkinningWeights(mesh); } catch (_) {}
          }
          experiment.setSelectedBones([{
            sourceKey: 'test/bodyblend.buf|offset=0',
            sourceFile: 'Test/BodyBlend.buf', boneIdOffset: 0, boneIds: [1],
          }]);
          const finalState = await experiment.enableModelPhysics();
          const states = window.modViewer.activeMeshes.map(mesh => {
            const state = experiment.getSkinningState(mesh);
            return {
              status: state.physicsParticipantStatus,
              enabled: state.physicsEnabled,
              error: state.physicsParticipantError,
            };
          });
          const disabled = experiment.disableModelPhysics();
          return {
            state: finalState,
            states,
            disabled: experiment.getModelPhysicsState(),
            disabledStates: window.modViewer.activeMeshes.map(mesh =>
              experiment.getSkinningState(mesh).physicsEnabled),
          };
        }""")
        assert result["state"]["enabled"]
        assert result["state"]["participantCount"] == 1
        assert result["state"]["failedCount"] == 1
        assert sorted(item["status"] for item in result["states"]) == [
            "failed", "participating"]
        assert all(result["disabledStates"]) is False
        assert not result["disabled"]["enabled"]
        assert result["disabled"]["participantCount"] == 0
    finally:
        context.close()


def _invalidation_payload():
    payload = _payload("Invalidation")
    template = next(iter(payload["meshes"].values()))

    def condition(variable, value):
        return [[{"var": variable, "value": value, "negate": False}]]

    def mesh(name, *, conditions=None, texture_variants=None, shape=None):
        entry = copy.deepcopy(template)
        entry["component"] = name
        entry["conditions"] = conditions or []
        entry["texture_variants"] = texture_variants or []
        entry["shape_targets"] = [] if shape is None else [{
            "var": shape,
            "pos": _f32(0, 0, 0, 1.5, 0, 0, 0, 1.5, 0),
        }]
        return entry

    alt_texture = payload["texture_pools"]["p0"][1]["tex_key"]
    payload["meshes"] = {
        "Mesh-A": mesh("Mesh A", conditions=condition("visibleA", "1"),
                        shape="shapeA"),
        "Mesh-B": mesh("Mesh B", texture_variants=[{
            "conditions": condition("textureB", "1"),
            "tex_key": alt_texture,
        }], shape="shapeB"),
        "Mesh-C": mesh("Mesh C"),
    }
    payload["controls"]["menu"] = {}
    payload["state"]["defaults"].update({
        "visibleA": "1", "textureB": "0", "shapeA": "0", "shapeB": "0",
    })
    return payload


def test_control_dependency_and_snapshot_helpers(edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"Deps": _payload("Deps")})
    try:
        result = page.evaluate("""async () => {
          const visibility = await import('./js/mesh/visibility.js');
          const controls = await import('./js/editing/control-state.js');
          const mesh = {userData: {
            conditions: [[{var: 'A'}, {var: 'B'}], [{var: 'C'}]],
            textureVariants: [{conditions: [[{var: 'D'}]]}],
            normalMapVariants: [{conditions: [[{var: 'E'}]]}],
            emissionMapVariants: [{conditions: [[{var: 'F'}]]}],
            shapeTargets: [{var: 'ShapeA'}, {var: 'ShapeB'}, {var: 'ShapeA'}],
          }};
          const first = visibility.dependenciesFor(mesh);
          const cached = visibility.dependenciesFor(mesh) === first;
          mesh.userData.conditions = [[{var: 'NewVisibility'}]];
          const beforeInvalidate = [...visibility.dependenciesFor(mesh).visibility];
          visibility.invalidateControlDependencies(mesh);
          const afterInvalidate = [...visibility.dependenciesFor(mesh).visibility];
          const changed = [...controls.changedControlVariables(
            {A: '0', B: '1'}, {A: '1', B: '1', C: '0'})];
          const removed = [...controls.changedControlVariables(
            {A: '1', B: '1'}, {A: '1'})];
          return {
            conditionVariables: [...visibility.variablesFromConditions(
              mesh.userData.conditions)],
            textureVariables: [...first.textures],
            shapeVariables: [...first.shapes],
            cached, beforeInvalidate, afterInvalidate, changed, removed,
          };
        }""")
        assert result == {
            "conditionVariables": ["NewVisibility"],
            "textureVariables": ["D", "E", "F"],
            "shapeVariables": ["ShapeA", "ShapeB"],
            "cached": True,
            "beforeInvalidate": ["A", "B", "C"],
            "afterInvalidate": ["NewVisibility"],
            "changed": ["A", "C"],
            "removed": ["B"],
        }
    finally:
        context.close()


def test_control_refresh_updates_only_affected_mesh_categories(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"Invalidation": _invalidation_payload()})
    try:
        _open(page, "Invalidation")
        page.locator(".draw-item").nth(2).wait_for()
        page.wait_for_timeout(100)
        result = page.evaluate("""async () => {
          const {setControlValue} = await import('./js/editing/control-state.js');
          const {refreshAll} = await import('./js/mesh/visibility.js');
          const meshes = window.modViewer.activeMeshes;
          const refs = meshes.map(mesh => ({
            mesh, geometry: mesh.geometry, material: mesh.material,
          }));
          const capture = () => meshes.map(mesh => ({
            name: mesh.userData.semanticKey,
            visible: mesh.visible,
            positionVersion: mesh.geometry.attributes.position.version,
            resolved: mesh.userData.resolvedTexKey,
          }));
          const initial = capture();
          const stable = refreshAll();
          const noOp = capture();

          setControlValue('visibleA', '0');
          const visibility = refreshAll();
          const afterVisibility = capture();

          setControlValue('shapeA', '1');
          const shape = refreshAll();
          const afterShape = capture();

          setControlValue('textureB', '1');
          const texture = refreshAll();
          const afterTexture = capture();
          const sameObjects = refs.every((ref, index) =>
            meshes[index] === ref.mesh && meshes[index].geometry === ref.geometry
              && meshes[index].material === ref.material);
          const names = result => result.changedMeshes.map(mesh => mesh.userData.semanticKey);
          return {
            stable: {
              visibilityChanged: stable.visibilityChanged,
              texturesChanged: stable.texturesChanged,
              shapesChanged: stable.shapesChanged,
              state: noOp,
            },
            visibility: {
              names: names(visibility), state: afterVisibility,
            },
            shape: {names: names(shape), state: afterShape},
            texture: {names: names(texture), state: afterTexture},
            initial, sameObjects,
          };
        }""")
        assert result["stable"] == {
            "visibilityChanged": False, "texturesChanged": False,
            "shapesChanged": False, "state": result["initial"],
        }
        assert result["visibility"]["names"] == ["Mesh-A"]
        assert result["visibility"]["state"][0]["visible"] is False
        assert [item["positionVersion"] for item in result["visibility"]["state"]] == [
            item["positionVersion"] for item in result["initial"]]
        assert result["shape"]["names"] == ["Mesh-A"]
        assert result["shape"]["state"][0]["positionVersion"] > \
            result["initial"][0]["positionVersion"]
        assert [item["positionVersion"] for item in result["shape"]["state"][1:]] == [
            item["positionVersion"] for item in result["initial"][1:]]
        assert result["texture"]["names"] == ["Mesh-B"]
        assert result["texture"]["state"][1]["resolved"] == \
            "diffuse::Invalidation-two.png"
        assert [item["positionVersion"] for item in result["texture"]["state"]] == [
            item["positionVersion"] for item in result["shape"]["state"]]
        assert result["sameObjects"]
        assert page.evaluate("window.__fakeApi.calls.loadMod") == ["Invalidation"]
    finally:
        context.close()


def test_control_refresh_plans_all_final_values_from_derived_rules(
        edge_browser, frontend_url):
    payload = _invalidation_payload()
    payload["state"]["defaults"].update({
        "root": "0", "visibleA": "1", "shapeA": "0", "textureB": "0",
    })
    payload["state"]["rules"] = [
        {"conditions": [[{"var": "root", "value": "1"}]],
         "var": "visibleA", "value": "0"},
        {"conditions": [[{"var": "visibleA", "value": "0"}]],
         "var": "shapeA", "value": "1"},
        {"conditions": [[{"var": "shapeA", "value": "1"}]],
         "var": "textureB", "value": "1"},
    ]
    context, page = _page(
        edge_browser, frontend_url, {"Derived": payload})
    try:
        _open(page, "Derived")
        page.locator(".draw-item").nth(2).wait_for()
        result = page.evaluate("""async () => {
          const {setControlValue} = await import('./js/editing/control-state.js');
          const {refreshAll} = await import('./js/mesh/visibility.js');
          const before = window.modViewer.activeMeshes.map(mesh => ({
            visible: mesh.visible,
            positionVersion: mesh.geometry.attributes.position.version,
            texture: mesh.userData.resolvedTexKey,
          }));
          setControlValue('root', '1');
          const refresh = refreshAll();
          const after = window.modViewer.activeMeshes.map(mesh => ({
            visible: mesh.visible,
            positionVersion: mesh.geometry.attributes.position.version,
            texture: mesh.userData.resolvedTexKey,
          }));
          return {
            changed: [...refresh.changedMeshes].map(mesh => mesh.userData.semanticKey),
            visibilityChanged: refresh.visibilityChanged,
            shapesChanged: refresh.shapesChanged,
            texturesChanged: refresh.texturesChanged,
            before, after,
          };
        }""")
        assert result["changed"] == ["Mesh-A", "Mesh-B"]
        assert result["visibilityChanged"]
        assert result["shapesChanged"]
        assert result["texturesChanged"]
        assert not result["after"][0]["visible"]
        assert result["after"][0]["positionVersion"] > \
            result["before"][0]["positionVersion"]
        assert result["after"][1]["texture"] == "diffuse::Invalidation-two.png"
        assert result["after"][2] == result["before"][2]
    finally:
        context.close()


def test_noop_control_refresh_does_not_request_render(edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"NoOp": _payload("NoOp")})
    try:
        _open(page, "NoOp")
        page.locator(".draw-item").wait_for()
        page.wait_for_timeout(150)
        before = page.evaluate("""() => ({
          render: window.modViewer.getRenderCount(),
          positions: window.modViewer.activeMeshes.map(mesh =>
            mesh.geometry.attributes.position.version),
        })""")
        page.evaluate("import('./js/mesh/visibility.js').then(({refreshAll}) => refreshAll())")
        page.wait_for_timeout(100)
        after = page.evaluate("""() => ({
          render: window.modViewer.getRenderCount(),
          positions: window.modViewer.activeMeshes.map(mesh =>
            mesh.geometry.attributes.position.version),
        })""")
        assert after == before
    finally:
        context.close()


def test_clean_control_refresh_skips_texture_run_reconciliation(
        edge_browser, frontend_url):
    payload = _invalidation_payload()
    context, page = _page(
        edge_browser, frontend_url, {"TextureRuns": payload})
    try:
        _open(page, "TextureRuns")
        page.locator(".draw-item").nth(2).wait_for()
        result = page.evaluate("""async () => {
          const {setControlValue} = await import('./js/editing/control-state.js');
          const {refreshAll} = await import('./js/mesh/visibility.js');
          const pool = window.modViewer.activeMeshes[0].userData.texturePool;
          const marker = 'light::reconciliation-marker';
          const markAndRefresh = () => {
            pool[0].light_map = marker;
            refreshAll();
            return Object.hasOwn(pool[0], 'light_map');
          };
          const noOp = markAndRefresh();
          setControlValue('visibleA', '0');
          const visibility = markAndRefresh();
          setControlValue('shapeA', '1');
          const shape = markAndRefresh();
          setControlValue('textureB', '1');
          const texture = markAndRefresh();
          return {noOp, visibility, shape, texture};
        }""")
        assert result == {
            "noOp": True, "visibility": True, "shape": True,
            "texture": False,
        }
    finally:
        context.close()


def test_skinning_angular_motion_uses_full_quaternion_delta(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"AngularLifecycle3D": _payload("AngularLifecycle3D")})
    try:
        _open(page, "AngularLifecycle3D")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        result = page.evaluate("""async () => {
          const THREE = await import('three');
          const experiment = await import('./js/mesh/weight-experiment.js');
          const mesh = window.modViewer.activeMeshes[0];
          const bytes = new Uint8Array(48);
          new Uint32Array(bytes.buffer).set([0, 1, 1, 2, 0, 2]);
          new Float32Array(bytes.buffer, 24).set([.8, .2, .7, .3, .6, .4]);
          const url = URL.createObjectURL(new Blob([bytes]));
          window.pywebview.api.get_skinning_preview = async () => ({
            status: 'ok', vertex_count: 3, influence_count: 2,
            bone_ids: [0, 1, 2], encoding: 'test', source: {
              key: 'test/bodyblend.buf|offset=0', file: 'Test/BodyBlend.buf',
              bone_id_offset: 0,
            },
            data: {
              url, length: 48,
              indices: {offset: 0, length: 24, type: 'u32'},
              weights: {offset: 24, length: 24, type: 'f32'},
            }, diagnostics: {},
          });
          await experiment.loadSkinningWeights(mesh);
          experiment.setSelectedBones([{
            sourceKey: 'test/bodyblend.buf|offset=0',
            sourceFile: 'Test/BodyBlend.buf', boneIdOffset: 0, boneIds: [1],
          }]);
          const originalRequestAnimationFrame = window.requestAnimationFrame;
          const originalCancelAnimationFrame = window.cancelAnimationFrame;
          const queuedFrames = [];
          window.requestAnimationFrame = callback => {
            queuedFrames.push(callback);
            return callback;
          };
          window.cancelAnimationFrame = callback => {
            const index = queuedFrames.indexOf(callback);
            if (index >= 0) queuedFrames.splice(index, 1);
          };
          const runFrames = timestamp => {
            queuedFrames.splice(0).forEach(callback => callback(timestamp));
          };
          experiment.setPhysicsEnabled(mesh, true);
          runFrames(0);
          runFrames(16.7);
          const before = [...mesh.geometry.attributes.position.array];
          mesh.quaternion.copy(new THREE.Quaternion().setFromEuler(
            new THREE.Euler(.2, .3, .4, 'XYZ')));
          window.dispatchEvent(new CustomEvent(
            'mod-viewer-model-transform-changed', {
              detail: {meshes: [mesh], reason: 'test-rotation'},
            }));
          const state = experiment.getSkinningState(mesh);
          const delta = state.lastRootAngularDeltaVector;
          const jointVectors = [...state.physicsState.joints.values()]
            .map(joint => joint.rotationVector);
          runFrames(33.4);
          const after = experiment.getSkinningState(mesh);
          window.requestAnimationFrame = originalRequestAnimationFrame;
          window.cancelAnimationFrame = originalCancelAnimationFrame;
          URL.revokeObjectURL(url);
          return {
            delta,
            deltaMagnitude: state.lastRootAngularDeltaMagnitude,
            jointVectors,
            geometryChanged: mesh.geometry.attributes.position.array.some(
              (value, index) => Math.abs(value - before[index]) > 1e-6),
            scheduled: experiment.isPhysicsScheduled(mesh),
            settled: after.physicsSettled,
          };
        }""")
        assert all(abs(value) > 1e-5 for value in result["delta"])
        assert result["deltaMagnitude"] > .1
        assert any(
            sum(abs(value) for value in vector) > 1e-5
            for vector in result["jointVectors"])
        assert result["geometryChanged"]
        assert result["scheduled"]
    finally:
        context.close()


def test_skinning_translation_gravity_limits_and_cleanup_use_vector_state(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"MotionLifecycle3D": _payload("MotionLifecycle3D")})
    try:
        _open(page, "MotionLifecycle3D")
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
          window.pywebview.api.get_skinning_preview = async () => ({
            status: 'ok', vertex_count: 3, influence_count: 2,
            bone_ids: [0, 1, 2], encoding: 'test', source: {
              key: 'test/bodyblend.buf|offset=0', file: 'Test/BodyBlend.buf',
              bone_id_offset: 0,
            },
            data: {
              url, length: 48,
              indices: {offset: 0, length: 24, type: 'u32'},
              weights: {offset: 24, length: 24, type: 'f32'},
            }, diagnostics: {},
          });
          await experiment.loadSkinningWeights(mesh);
          experiment.setSelectedBones([{
            sourceKey: 'test/bodyblend.buf|offset=0',
            sourceFile: 'Test/BodyBlend.buf', boneIdOffset: 0, boneIds: [1],
          }]);
          const originalRequestAnimationFrame = window.requestAnimationFrame;
          const originalCancelAnimationFrame = window.cancelAnimationFrame;
          const queuedFrames = [];
          window.requestAnimationFrame = callback => {
            queuedFrames.push(callback);
            return callback;
          };
          window.cancelAnimationFrame = callback => {
            const index = queuedFrames.indexOf(callback);
            if (index >= 0) queuedFrames.splice(index, 1);
          };
          const runFrames = timestamp => {
            queuedFrames.splice(0).forEach(callback => callback(timestamp));
          };
          experiment.setPhysicsEnabled(mesh, true);
          runFrames(0);
          runFrames(16.7);
          const cameraBefore = scene.camera.position.clone();
          const positionBefore = mesh.position.clone();
          scene.translateModel([mesh], [0, 0, .25]);
          const translated = experiment.getSkinningState(mesh);
          const translationVector = [...translated.lastTranslationLagRotationVector];
          const translationMagnitude = translated.lastTranslationLagRotationMagnitude;
          experiment.setPhysicsGravityEnabled(mesh, true);
          const gravity = experiment.getSkinningState(mesh);
          const gravityVector = [...gravity.physicsGravityAccelerations.get(1)];
          const gravityMax = gravity.physicsGravityDiagnostics
            .maxTotalAccelerationMagnitude;
          experiment.setPhysicsConstraintsEnabled(mesh, true);
          experiment.setPhysicsMaxBendDegrees(mesh, 10);
          const constrained = experiment.getSkinningState(mesh);
          const limits = constrained.physicsJointLimits instanceof Map;
          const beforeReset = [...constrained.physicsState.joints.values()]
            .map(joint => ({
              rotationVector: joint.rotationVector,
              angularVelocity: joint.angularVelocity,
            }));
          experiment.resetPhysicsMotion(mesh);
          const reset = experiment.getSkinningState(mesh);
          const resetJoints = [...reset.physicsState.joints.values()];
          const resetKeepsEnabled = reset.physicsEnabled;
          experiment.setPhysicsEnabled(mesh, false);
          const disabled = experiment.getSkinningState(mesh);
          window.requestAnimationFrame = originalRequestAnimationFrame;
          window.cancelAnimationFrame = originalCancelAnimationFrame;
          URL.revokeObjectURL(url);
          return {
            moved: !mesh.position.equals(positionBefore),
            cameraUnchanged: scene.camera.position.equals(cameraBefore),
            translationVector,
            translationMagnitude,
            gravityVector,
            gravityMax,
            limits,
            beforeReset,
            reset: resetJoints,
            resetKeepsEnabled,
            disabled: {
              enabled: disabled.physicsEnabled,
              reference: disabled.physicsReferenceQuaternion,
            },
          };
        }""")
        assert result["moved"]
        assert result["cameraUnchanged"]
        assert result["translationMagnitude"] > 1e-5
        assert any(abs(value) > 1e-5 for value in result["translationVector"])
        assert result["gravityVector"]
        assert result["gravityMax"] > 0
        assert result["limits"]
        assert any(
            sum(abs(value) for value in joint["rotationVector"]) > 1e-5
            for joint in result["beforeReset"])
        assert all(
            joint["rotationVector"] == [0, 0, 0]
            and joint["angularVelocity"] == [0, 0, 0]
            for joint in result["reset"])
        assert result["resetKeepsEnabled"]
        assert result["disabled"] == {"enabled": False, "reference": None}
    finally:
        context.close()





def test_weight_panel_loads_model_weights_and_controls_selected_bones(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"WeightPanel": _payload("WeightPanel")})
    try:
        _open(page, "WeightPanel")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        page.evaluate("""async () => {
          const bytes = new Uint8Array(48);
          new Uint32Array(bytes.buffer).set([0, 1, 1, 2, 0, 2]);
          new Float32Array(bytes.buffer, 24).set([.8, .2, .7, .3, .6, .4]);
          const url = URL.createObjectURL(new Blob([bytes]));
          const key = window.modViewer.activeMeshes[0].userData.semanticKey;
          window.__weightPreviewCalls = 0;
          window.pywebview.api.get_model_skinning_preview = async () => {
            window.__weightPreviewCalls += 1;
            return {
              status: 'ok', format_version: 1,
              data: {url, length: 48},
              meshes: {[key]: {
                status: 'ok', vertex_count: 3, influence_count: 2,
                bone_ids: [0, 1, 2], encoding: 'test', source: {
                  key: 'test/bodyblend.buf|offset=0', file: 'Test/BodyBlend.buf',
                  bone_id_offset: 0,
                },
                data: {
                  indices: {offset: 0, length: 24, type: 'u32'},
                  weights: {offset: 24, length: 24, type: 'f32'},
                }, diagnostics: {},
              }},
            };
          };
        }""")
        assert page.evaluate("window.modViewer.getModelWeightState().selectedBoneCount") == 0
        page.locator("#weight-tab").click()
        page.wait_for_function("window.__weightPreviewCalls === 1")
        page.wait_for_function("window.modViewer.getModelWeightState().loaded")
        mask_count_before = page.evaluate("""async () =>
          (await import('./js/mesh/weight-experiment.js'))
            .getSelectedWeightMaskBuildCount()""")
        assert page.locator("#inspector-panel .weight-section").count() == 0
        assert page.locator(".weight-bone-select").inner_text() == "Select bones"

        page.locator(".weight-bone-select").click()
        assert page.locator(".weight-bone-option").evaluate_all("""
          nodes => nodes.map(node => [
            node.querySelector('.weight-bone-id').textContent,
            node.querySelector('.weight-bone-meta').textContent,
          ])
        """) == [
            ["0", "2 verts · 70%"],
            ["1", "2 verts · 45%"],
            ["2", "2 verts · 35%"],
        ]
        assert page.locator("#weight-panel .inspector-label").count() == 0
        assert page.locator("#weight-panel .inspector-value").count() == 0
        assert page.locator("#weight-panel .inspector-section-title").count() == 0
        stats_build_count = page.evaluate("""async () =>
          (await import('./js/mesh/weight-experiment.js'))
            .getModelBoneStatsBuildCount()""")
        page.locator('.weight-bone-option input[value="1"]').check()
        page.wait_for_function("""() =>
          window.modViewer.getModelWeightState().selectedBoneCount === 1""")
        mask_count_after = page.evaluate("""async () =>
          (await import('./js/mesh/weight-experiment.js'))
            .getSelectedWeightMaskBuildCount()""")
        assert mask_count_after - mask_count_before == 1
        mask = page.evaluate("""async () => {
          const mesh = window.modViewer.activeMeshes[0];
          const {getSkinningState} = await import('./js/mesh/weight-experiment.js');
          return [...getSkinningState(mesh).selectedWeightMask];
        }""")
        assert mask == pytest.approx([.2, .7, 0])

        page.locator(".draw-item").click()
        assert page.locator("#weight-tab").get_attribute("aria-selected") == "true"
        page.locator(".weight-heatmap-enable").check()
        assert page.evaluate("window.modViewer.getModelWeightState().heatmapEnabled")
        mask_count_before = page.evaluate("""async () =>
          (await import('./js/mesh/weight-experiment.js'))
            .getSelectedWeightMaskBuildCount()""")
        page.locator("#weight-tab").click()
        page.locator("#weight-tab").click()
        page.wait_for_function("window.modViewer.getModelWeightState().heatmapEnabled")
        page.locator(".weight-bone-select").click()
        page.locator('.weight-bone-option input[value="2"]').check()
        page.wait_for_function("""() =>
          window.modViewer.getModelWeightState().selectedBoneCount === 2""")
        mask_count_after = page.evaluate("""async () =>
          (await import('./js/mesh/weight-experiment.js'))
            .getSelectedWeightMaskBuildCount()""")
        assert mask_count_after - mask_count_before == 1
        page.locator(".weight-bone-select").click()
        assert page.evaluate("window.__weightPreviewCalls") == 1
        assert page.evaluate("""async () =>
          (await import('./js/mesh/weight-experiment.js'))
            .getModelBoneStatsBuildCount()""") == stats_build_count
        page.wait_for_function("window.modViewer.getModelPhysicsState().enabled")
        physics = page.evaluate("""async () => {
          const {getSkinningState} = await import('./js/mesh/weight-experiment.js');
          const state = getSkinningState(window.modViewer.activeMeshes[0]);
          return {enabled: state.physicsEnabled,
            dynamic: state.physicsForest?.components?.[0]?.dynamicNodeIds || []};
        }""")
        assert physics["enabled"]
        assert physics["dynamic"] == [1, 2]
        page.locator(".weight-clear-selection").click()
        page.wait_for_function("!window.modViewer.getModelPhysicsState().enabled")
        assert page.evaluate("window.__weightPreviewCalls") == 1
        assert page.evaluate("window.modViewer.getModelWeightState().selectedBoneCount") == 0
    finally:
        page.evaluate("""() => {
          if (window.__weightPreviewCalls) window.__weightPreviewCalls = 0;
        }""")
        context.close()


def test_weight_picker_discovers_influences_without_mutating_selection(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"WeightPick": _payload("WeightPick")})
    try:
        _open(page, "WeightPick")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        page.evaluate("""async () => {
          const bytes = new Uint8Array(48);
          new Uint32Array(bytes.buffer).set([0, 1, 1, 2, 0, 2]);
          new Float32Array(bytes.buffer, 24).set([.8, .2, .7, .3, .6, .4]);
          const url = URL.createObjectURL(new Blob([bytes]));
          const key = window.modViewer.activeMeshes[0].userData.semanticKey;
          window.pywebview.api.get_model_skinning_preview = async () => ({
            status: 'ok', format_version: 1, saved_bones: [],
            data: {url, length: 48},
            meshes: {[key]: {
              status: 'ok', vertex_count: 3, influence_count: 2,
              bone_ids: [0, 1, 2], encoding: 'test', source: {
                key: 'test/bodyblend.buf|offset=0', file: 'Test/BodyBlend.buf',
                bone_id_offset: 0,
              },
              data: {
                indices: {offset: 0, length: 24, type: 'u32'},
                weights: {offset: 24, length: 24, type: 'f32'},
              }, diagnostics: {},
            }},
          });
        }""")
        page.locator("#weight-tab").click()
        page.wait_for_function("window.modViewer.getModelWeightState().loaded")
        page.locator(".weight-bone-select").click()
        page.locator('.weight-bone-option input[value="1"]').check()
        page.wait_for_function(
            "window.modViewer.getModelWeightState().selectedBoneCount === 1")
        page.locator(".weight-pick-model").click()
        assert page.locator(".weight-bone-popover").is_hidden()
        assert page.locator(".weight-pick-model").get_attribute("aria-pressed") == "true"
        assert "active" in (page.locator(".weight-pick-model").get_attribute("class") or "")
        assert page.evaluate(
            "getComputedStyle(document.querySelector('#canvas-container canvas')).cursor") == "crosshair"
        point = page.evaluate("""async () => {
          const THREE = await import('three');
          const {camera, renderer} = await import('./js/scene/scene.js');
          const mesh = window.modViewer.activeMeshes[0];
          const projected = new THREE.Vector3(.25, .25, 0)
            .applyMatrix4(mesh.matrixWorld).project(camera);
          const rect = renderer.domElement.getBoundingClientRect();
          return {
            x: rect.left + (projected.x + 1) * rect.width / 2,
            y: rect.top + (1 - projected.y) * rect.height / 2,
          };
        }""")
        page.mouse.click(point["x"], point["y"])
        page.wait_for_function(
            "window.modViewer.getModelWeightState().pickerViewMode === 'picked'")
        result = page.evaluate("""() => {
          const state = window.modViewer.getModelWeightState();
          return {
            selected: state.selectedBones,
            picked: state.pickedPoint,
            mode: state.pickerViewMode,
            rows: [...document.querySelectorAll('.weight-bone-option')]
              .map(row => row.dataset.boneId),
            cursor: getComputedStyle(
              document.querySelector('#canvas-container canvas')).cursor,
            picking: state.picking,
            popoverOpen: !document.querySelector('.weight-bone-popover').hidden,
            expanded: document.querySelector('.weight-bone-select')
              .getAttribute('aria-expanded'),
            pickPressed: document.querySelector('.weight-pick-model')
              .getAttribute('aria-pressed'),
            pickedActive: document.querySelector('.weight-picker-view-option'
              + '[data-mode="picked"]').classList.contains('active'),
            scrollTop: document.querySelector('.weight-bone-list').scrollTop,
          };
        }""")
        assert result["selected"] == [{
            "sourceKey": "test/bodyblend.buf|offset=0",
            "sourceFile": "Test/BodyBlend.buf", "boneIdOffset": 0,
            "boneIds": [1],
        }]
        assert result["picked"]["sourceKey"] == "test/bodyblend.buf|offset=0"
        assert result["picked"]["influences"]
        assert result["mode"] == "picked"
        assert result["rows"]
        assert result["cursor"] in ("auto", "")
        assert not result["picking"]
        assert result["popoverOpen"]
        assert result["expanded"] == "true"
        assert result["pickPressed"] == "false"
        assert result["pickedActive"]
        assert result["scrollTop"] == 0

        previous_pick = result["picked"]
        page.locator(".weight-pick-model").click()
        page.keyboard.press("Escape")
        page.wait_for_function(
            "!window.modViewer.getModelWeightState().picking")
        assert page.evaluate(
            "window.modViewer.getModelWeightState().pickedPoint") == previous_pick
        assert page.locator(".weight-bone-popover").is_hidden()
        assert page.locator(".weight-pick-model").get_attribute(
            "aria-pressed") == "false"

        page.locator(".weight-pick-model").click()
        page.evaluate("""() => {
          const canvas = document.querySelector('#canvas-container canvas');
          canvas.dispatchEvent(new PointerEvent('pointerdown', {
            bubbles: true, button: 0, pointerId: 77,
            clientX: 10, clientY: 10,
          }));
          canvas.dispatchEvent(new PointerEvent('pointercancel', {
            bubbles: true, pointerId: 77,
          }));
        }""")
        page.wait_for_function(
            "!window.modViewer.getModelWeightState().picking")
        cancelled = page.evaluate(
            "window.modViewer.getModelWeightState()")
        assert cancelled["pickedPoint"] == previous_pick
        assert cancelled["pickStatus"] == ""
    finally:
        context.close()


def test_weight_panel_preserves_picker_and_slider_dom_during_state_changes(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"WeightStable": _payload("WeightStable")})
    try:
        _open(page, "WeightStable")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        page.evaluate("""async () => {
          const bytes = new Uint8Array(48);
          new Uint32Array(bytes.buffer).set([0, 1, 1, 2, 0, 2]);
          new Float32Array(bytes.buffer, 24).set([.8, .2, .7, .3, .6, .4]);
          const url = URL.createObjectURL(new Blob([bytes]));
          const key = window.modViewer.activeMeshes[0].userData.semanticKey;
          window.pywebview.api.get_model_skinning_preview = async () => ({
            status: 'ok', format_version: 1, data: {url, length: 48},
            meshes: {[key]: {
              status: 'ok', vertex_count: 3, influence_count: 2,
              bone_ids: [0, 1, 2], encoding: 'test', source: {
                key: 'test/bodyblend.buf|offset=0', file: 'Test/BodyBlend.buf',
                bone_id_offset: 0,
              },
              data: {
                indices: {offset: 0, length: 24, type: 'u32'},
                weights: {offset: 24, length: 24, type: 'f32'},
              }, diagnostics: {},
            }},
          });
        }""")
        page.locator("#weight-tab").click()
        page.wait_for_function("window.modViewer.getModelWeightState().loaded")
        page.locator(".weight-bone-select").click()
        page.locator(".weight-bone-search").fill("1")
        assert page.evaluate("""() => {
          const button = document.querySelector('.weight-bone-select');
          const option = document.querySelector('.weight-bone-option');
          const filter = document.querySelector('.weight-bone-filter');
          const search = document.querySelector('.weight-bone-search');
          return [button, option, filter, search].map(
            node => getComputedStyle(node).fontSize);
        }""") == ["12px", "12px", "12px", "12px"]
        page.evaluate("window.__weightPicker = document.querySelector('.weight-bone-popover')")
        page.locator('.weight-bone-option input[value="1"]').check()
        page.wait_for_function("""() =>
          window.modViewer.getModelWeightState().selectedBoneCount === 1""")
        assert page.evaluate("window.__weightPicker === document.querySelector('.weight-bone-popover')")
        assert page.locator(".weight-bone-select").get_attribute("aria-expanded") == "true"
        assert page.locator(".weight-bone-search").input_value() == "1"

        page.locator(".weight-bone-search").fill("")
        page.locator('.weight-bone-option input[value="2"]').check()
        page.wait_for_function("""() =>
          window.modViewer.getModelWeightState().selectedBoneCount === 2""")
        assert page.evaluate("window.__weightPicker === document.querySelector('.weight-bone-popover')")
        page.locator(".weight-selected-only").check()
        assert page.locator(".weight-bone-option").evaluate_all("""nodes =>
          nodes.filter(node => !node.hidden).map(node => ({
            id: node.dataset.boneId,
            display: getComputedStyle(node).display,
          }))""") == [
            {"id": "1", "display": "flex"},
            {"id": "2", "display": "flex"},
        ]
        page.locator(".weight-bone-search").fill("2")
        page.locator('.weight-bone-option input[value="2"]').uncheck()
        page.wait_for_function("""() =>
          window.modViewer.getModelWeightState().selectedBoneCount === 1""")
        assert page.locator(".weight-bone-search").input_value() == "2"
        assert page.locator(".weight-selected-only").is_checked()
        assert page.locator(".weight-bone-option").evaluate_all(
            "nodes => nodes.every(node => node.hidden && getComputedStyle(node).display === 'none')")
        assert page.locator(".weight-bone-select").get_attribute("aria-expanded") == "true"
        page.locator(".weight-bone-select").click()

        slider = page.locator(".weight-physics-frequency")
        page.evaluate("window.__weightSlider = document.querySelector('.weight-physics-frequency')")
        page.evaluate("""() => {
          const input = document.querySelector('.weight-physics-frequency');
          input.value = '3.5';
          input.dispatchEvent(new Event('input', {bubbles: true}));
        }""")
        page.wait_for_function("window.modViewer.getModelPhysicsState().frequencyHz === 3.5")
        assert page.evaluate("window.__weightSlider === document.querySelector('.weight-physics-frequency')")
        assert slider.input_value() == "3.5"

        page.wait_for_function("window.modViewer.getModelPhysicsState().enabled")
        writes = page.evaluate("""() => {
          const input = document.querySelector('.weight-bone-option input');
          const checked = Object.getOwnPropertyDescriptor(
            HTMLInputElement.prototype, 'checked');
          let writes = 0;
          Object.defineProperty(input, 'checked', {
            configurable: true,
            get: () => checked.get.call(input),
            set: value => { writes += 1; checked.set.call(input, value); },
          });
          for (let i = 0; i < 20; i += 1) {
            window.dispatchEvent(new CustomEvent('mod-viewer-model-physics-changed'));
          }
          return writes;
        }""")
        assert writes == 0
        assert page.evaluate("window.__weightSlider === document.querySelector('.weight-physics-frequency')")
        page.locator(".weight-bone-select").click()
        assert page.locator(".weight-bone-select").get_attribute("aria-expanded") == "true"
        page.locator("#inspector-tab").click()
        assert page.locator(".weight-bone-popover").is_hidden()
        page.locator("#weight-tab").click()
        assert page.evaluate("window.__weightPicker === document.querySelector('.weight-bone-popover')")
    finally:
        context.close()


def test_weight_saved_selection_applies_once_and_controls_physics(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"WeightSaved": _payload("WeightSaved")})
    try:
        _open(page, "WeightSaved")
        page.wait_for_function("window.modViewer.activeMeshes.length === 1")
        page.evaluate("""async () => {
          const bytes = new Uint8Array(48);
          new Uint32Array(bytes.buffer).set([0, 1, 1, 2, 0, 2]);
          new Float32Array(bytes.buffer, 24).set([.8, .2, .7, .3, .6, .4]);
          const url = URL.createObjectURL(new Blob([bytes]));
          const key = window.modViewer.activeMeshes[0].userData.semanticKey;
          window.__savedSelections = [];
          window.pywebview.api.get_model_skinning_preview = async () => ({
            status: 'ok', format_version: 1,
            saved_bones: [{source: 'Test/BodyBlend.buf', bone_id_offset: 0,
              bone_ids: [99, 1]}],
            data: {url, length: 48},
            meshes: {[key]: {
              status: 'ok', vertex_count: 3, influence_count: 2,
              bone_ids: [0, 1, 2], encoding: 'test', source: {
                key: 'test/bodyblend.buf|offset=0', file: 'Test/BodyBlend.buf',
                bone_id_offset: 0,
              },
              data: {
                indices: {offset: 0, length: 24, type: 'u32'},
                weights: {offset: 24, length: 24, type: 'f32'},
              }, diagnostics: {}, source: {
                key: 'test/bodyblend.buf|offset=0',
                file: 'Test/BodyBlend.buf', bone_id_offset: 0,
              },
            }},
          });
          window.pywebview.api.save_weight_selection =
            async (_path, bones) => {
              window.__savedSelections.push([...bones]);
              return {saved: true, selected_bones: bones};
            };
        }""")
        page.locator("#weight-tab").click()
        page.wait_for_function("""() =>
          window.modViewer.getModelWeightState().selectedBoneCount === 1""")
        page.wait_for_function("window.modViewer.getModelPhysicsState().enabled")

        page.locator(".weight-clear-selection").click()
        page.wait_for_function("!window.modViewer.getModelPhysicsState().enabled")
        assert page.evaluate(
            "window.modViewer.getModelWeightState().savedBones[0].boneIds") == [1, 99]
        page.evaluate("""() => {
          for (const [selector, value] of [
            ['.weight-physics-frequency', '7'],
            ['.weight-physics-damping', '1.2'],
          ]) {
            const input = document.querySelector(selector);
            input.value = value;
            input.dispatchEvent(new Event('input', {bubbles: true}));
          }
        }""")
        assert not page.locator(".weight-physics-reset").is_disabled()
        page.locator(".weight-physics-reset").click()
        page.wait_for_function("""() => {
          const state = window.modViewer.getModelPhysicsState();
          return state.frequencyHz === 2 && state.dampingRatio === .35;
        }""")
        assert not page.evaluate("window.modViewer.getModelPhysicsState().enabled")
        assert page.evaluate(
            "window.modViewer.getModelWeightState().selectedBoneCount") == 0
        page.locator("#weight-tab").click()
        page.locator("#weight-tab").click()
        assert page.evaluate(
            "window.modViewer.getModelWeightState().selectedBoneCount") == 0

        page.locator(".weight-load-selection").click()
        page.wait_for_function("window.modViewer.getModelPhysicsState().enabled")
        page.locator(".weight-save-selection").click()
        page.wait_for_function("window.__savedSelections.length === 1")
        assert page.evaluate("window.__savedSelections") == [[{
            'source': 'Test/BodyBlend.buf', 'bone_id_offset': 0,
            'bone_ids': [1],
        }]]
        page.evaluate("""() => {
          window.pywebview.api.save_weight_selection = async () => {
            throw new Error('disk full');
          };
        }""")
        page.locator(".weight-save-selection").click()
        page.wait_for_function("""() =>
          window.modViewer.getModelWeightState().selectionSaveError === 'disk full'""")
        assert page.locator(".weight-status").inner_text() == (
            "Could not save bone selection: disk full")
    finally:
        context.close()


def test_weight_picker_ignores_mesh_selection(
        edge_browser, frontend_url):
    payload = _payload("WeightMeshFilter")
    first_name, first = next(iter(payload["meshes"].items()))
    second = copy.deepcopy(first)
    second["component"] = "Hair"
    payload["meshes"] = {first_name: first, "Hair-WeightMeshFilter-0": second}
    context, page = _page(
        edge_browser, frontend_url, {"WeightMeshFilter": payload})
    try:
        _open(page, "WeightMeshFilter")
        page.wait_for_function("window.modViewer.activeMeshes.length === 2")
        page.evaluate("""async () => {
          const bytes = new Uint8Array(96);
          for (const offset of [0, 48]) {
            new Uint32Array(bytes.buffer, offset, 6).set(
              offset ? [1, 2, 1, 2, 1, 2] : [0, 1, 0, 1, 0, 1]);
            new Float32Array(bytes.buffer, offset + 24, 6).set(
              [.8, .2, .7, .3, .6, .4]);
          }
          const url = URL.createObjectURL(new Blob([bytes]));
          const meshes = window.modViewer.activeMeshes;
          const entries = Object.fromEntries(meshes.map((mesh, index) => [
            mesh.userData.semanticKey,
            {
              status: 'ok', vertex_count: 3, influence_count: 2,
              bone_ids: index ? [1, 2] : [0, 1], encoding: 'test', source: {
                key: 'test/bodyblend.buf|offset=0', file: 'Test/BodyBlend.buf',
                bone_id_offset: 0,
              },
              data: {
                indices: {offset: index * 48, length: 24, type: 'u32'},
                weights: {offset: index * 48 + 24, length: 24, type: 'f32'},
              }, diagnostics: {},
            },
          ]));
          window.pywebview.api.get_model_skinning_preview = async () => ({
            status: 'ok', format_version: 1, saved_bones: [],
            data: {url, length: 96}, meshes: entries,
          });
        }""")
        page.locator("#weight-tab").click()
        page.wait_for_function("window.modViewer.getModelWeightState().loaded")
        page.locator(".weight-bone-select").click()
        assert page.locator(".weight-bone-option").count() == 3
        page.locator('.weight-bone-option input[value="2"]').check()
        page.evaluate("""async () => {
          const {selectMesh} = await import('./js/scene/selection.js');
          selectMesh(window.modViewer.activeMeshes[0]);
        }""")
        assert page.locator(".weight-bone-option").evaluate_all(
            "nodes => nodes.map(node => node.dataset.boneId)") == ["0", "1", "2"]
        assert page.evaluate(
            "window.modViewer.getModelWeightState().selectedBoneCount") == 1
        page.locator(".draw-item").nth(1).click()
        assert page.locator(".weight-bone-select").get_attribute(
            "aria-expanded") == "true"
        assert page.locator(".weight-bone-option").evaluate_all(
            "nodes => nodes.map(node => node.dataset.boneId)") == ["0", "1", "2"]
        page.evaluate("""async () => {
          const {clearSelection} = await import('./js/scene/selection.js');
          clearSelection();
        }""")
        assert page.locator(
            ".weight-picker-view-option[data-mode='all']").get_attribute(
                "aria-pressed") == "true"
        assert page.locator(
            ".weight-picker-view-option[data-mode='picked']").is_disabled()
    finally:
        context.close()


def test_weight_selection_is_scoped_to_the_decoded_blend_source(
        edge_browser, frontend_url):
    payload = _payload("WeightSources")
    first_name, first = next(iter(payload["meshes"].items()))
    second = copy.deepcopy(first)
    second["component"] = "Coat"
    payload["meshes"] = {
        first_name: first, "Coat-WeightSources-0": second,
    }
    context, page = _page(
        edge_browser, frontend_url, {"WeightSources": payload})
    try:
        _open(page, "WeightSources")
        page.wait_for_function("window.modViewer.activeMeshes.length === 2")
        page.evaluate("""async () => {
          const bytes = new Uint8Array(96);
          for (const [offset, ids] of [
            [0, [1, 0, 1, 0, 1, 0]],
            [48, [1, 0, 1, 0, 1, 0]],
          ]) {
            new Uint32Array(bytes.buffer, offset, 6).set(ids);
            new Float32Array(bytes.buffer, offset + 24, 6).set(
              [.8, .2, .7, .3, .6, .4]);
          }
          const url = URL.createObjectURL(new Blob([bytes]));
          const sources = [
            {key: 'hair/hairblend.buf|offset=0',
             file: 'Hair/HairBlend.buf', bone_id_offset: 0},
            {key: 'coat/coatblend.buf|offset=0',
             file: 'Coat/CoatBlend.buf', bone_id_offset: 0},
          ];
          const entries = Object.fromEntries(window.modViewer.activeMeshes.map(
            (mesh, index) => [mesh.userData.semanticKey, {
              status: 'ok', vertex_count: 3, influence_count: 2,
              bone_ids: [0, 1], encoding: 'test', source: sources[index],
              data: {
                indices: {offset: index * 48, length: 24, type: 'u32'},
                weights: {offset: index * 48 + 24, length: 24, type: 'f32'},
              }, diagnostics: {},
            }]));
          window.pywebview.api.get_model_skinning_preview = async () => ({
            status: 'ok', format_version: 1, saved_bones: [],
            data: {url, length: 96}, meshes: entries,
          });
        }""")
        page.locator("#weight-tab").click()
        page.wait_for_function("window.modViewer.getModelWeightState().loaded")
        page.locator(".weight-bone-select").click()
        assert page.locator(".weight-bone-group").count() == 2
        assert page.locator(".weight-bone-option").count() == 4
        page.locator('.weight-bone-option[data-source-key="hair/hairblend.buf|offset=0"] input[value="1"]').check()
        page.wait_for_function("window.modViewer.getModelPhysicsState().enabled")
        result = page.evaluate("""async () => {
          const {getSkinningState} = await import('./js/mesh/weight-experiment.js');
          const [hair, coat] = window.modViewer.activeMeshes.map(getSkinningState);
          return {
            selected: window.modViewer.getModelWeightState().selectedBones,
            masks: [hair.selectedWeightMask[0], coat.selectedWeightMask[0]],
            physics: window.modViewer.getModelPhysicsState(),
            participants: [hair.physicsEnabled, coat.physicsEnabled],
          };
        }""")
        assert result["selected"] == [{
            "sourceKey": "hair/hairblend.buf|offset=0",
            "sourceFile": "Hair/HairBlend.buf", "boneIdOffset": 0,
            "boneIds": [1],
        }]
        assert result["masks"] == pytest.approx([.8, 0])
        assert result["participants"] == [True, False]
        assert result["physics"]["participantCount"] == 1

        page.locator(".weight-heatmap-enable").check()
        assert page.evaluate("""async () => {
          const {getSkinningState} = await import('./js/mesh/weight-experiment.js');
          return window.modViewer.activeMeshes.map(mesh =>
            getSkinningState(mesh).heatmapMode);
        }""") == ["bone", None]
        distinct = page.evaluate("""async () => {
          const experiment = await import('./js/mesh/weight-experiment.js');
          experiment.setSelectedBones([
            {sourceKey: 'hair/hairblend.buf|offset=0',
             sourceFile: 'Hair/HairBlend.buf', boneIdOffset: 0, boneIds: [1]},
            {sourceKey: 'coat/coatblend.buf|offset=0',
             sourceFile: 'Coat/CoatBlend.buf', boneIdOffset: 0, boneIds: [1]},
          ]);
          const [hair, coat] = window.modViewer.activeMeshes.map(
            experiment.getSkinningState);
          return {
            physics: experiment.getModelPhysicsState(),
            independentState: hair.physicsState !== coat.physicsState,
            independentTransforms: hair.physicsTransforms
              !== coat.physicsTransforms,
          };
        }""")
        assert distinct["physics"]["participantCount"] == 2
        assert distinct["physics"]["participatingMeshCount"] == 2
        assert distinct["independentState"]
        assert distinct["independentTransforms"]
    finally:
        context.close()


def test_weight_selection_shared_source_participates_per_mesh(
        edge_browser, frontend_url):
    payload = _payload("WeightSharedSource")
    first_name, first = next(iter(payload["meshes"].items()))
    second = copy.deepcopy(first)
    second["component"] = "Hair"
    payload["meshes"] = {
        first_name: first, "Hair-WeightSharedSource-0": second,
    }
    context, page = _page(
        edge_browser, frontend_url, {"WeightSharedSource": payload})
    try:
        _open(page, "WeightSharedSource")
        page.wait_for_function("window.modViewer.activeMeshes.length === 2")
        page.evaluate("""async () => {
          const bytes = new Uint8Array(96);
          for (const offset of [0, 48]) {
            new Uint32Array(bytes.buffer, offset, 6).set(
              [1, 0, 1, 0, 1, 0]);
            new Float32Array(bytes.buffer, offset + 24, 6).set(
              [.8, .2, .7, .3, .6, .4]);
          }
          const url = URL.createObjectURL(new Blob([bytes]));
          const source = {key: 'shared/sharedblend.buf|offset=0',
            file: 'Shared/SharedBlend.buf', bone_id_offset: 0};
          const entries = Object.fromEntries(window.modViewer.activeMeshes.map(
            (mesh, index) => [mesh.userData.semanticKey, {
              status: 'ok', vertex_count: 3, influence_count: 2,
              bone_ids: [0, 1], encoding: 'test', source,
              data: {
                indices: {offset: index * 48, length: 24, type: 'u32'},
                weights: {offset: index * 48 + 24, length: 24, type: 'f32'},
              }, diagnostics: {},
            }]));
          window.pywebview.api.get_model_skinning_preview = async () => ({
            status: 'ok', format_version: 1, saved_bones: [],
            data: {url, length: 96}, meshes: entries,
          });
        }""")
        page.locator("#weight-tab").click()
        page.wait_for_function("window.modViewer.getModelWeightState().loaded")
        page.locator(".weight-bone-select").click()
        assert page.locator(".weight-bone-group").count() == 1
        assert page.locator(".weight-bone-option").count() == 2
        page.locator('.weight-bone-option[data-source-key="shared/sharedblend.buf|offset=0"] input[value="1"]').check()
        page.wait_for_function("window.modViewer.getModelPhysicsState().enabled")
        result = page.evaluate("""async () => {
          const {getSkinningState} = await import('./js/mesh/weight-experiment.js');
          const meshes = window.modViewer.activeMeshes;
          const [body, hair] = meshes.map(getSkinningState);
          const scene = await import('./js/scene/scene.js');
          scene.rotateModelQuarterTurn(meshes);
          const movedPositions = meshes.map(mesh =>
            [...mesh.geometry.attributes.position.array]);
          const hiddenMesh = meshes[1];
          hiddenMesh.visible = false;
          window.dispatchEvent(new CustomEvent(
            'mod-viewer-mesh-state-changed', {detail: {meshes: [hiddenMesh]}}));
          const hiddenState = window.modViewer.getModelPhysicsState();
          hiddenMesh.visible = true;
          window.dispatchEvent(new CustomEvent(
            'mod-viewer-mesh-state-changed', {detail: {meshes: [hiddenMesh]}}));
          const revealedPositions = [
            ...hiddenMesh.geometry.attributes.position.array];
          const maxSeamError = movedPositions[0].reduce((max, value, index) =>
            Math.max(max, Math.abs(value - movedPositions[1][index])), 0);
          const maxRevealError = revealedPositions.reduce((max, value, index) =>
            Math.max(max, Math.abs(value -
              meshes[0].geometry.attributes.position.array[index])), 0);
          return {
            sources: window.modViewer.getModelWeightState().sources,
            masks: meshes.map(mesh =>
              getSkinningState(mesh).selectedWeightMask[0]),
            physics: window.modViewer.getModelPhysicsState(),
            hiddenPhysics: hiddenState,
            participants: meshes.map(mesh =>
              getSkinningState(mesh).physicsEnabled),
            sharedState: body.physicsState === hair.physicsState,
            sharedTransforms: body.physicsTransforms === hair.physicsTransforms,
            sharedRotation: body.physicsRotations === hair.physicsRotations,
            maxSeamError,
            maxRevealError,
          };
        }""")
        assert len(result["sources"]) == 1
        assert result["masks"] == pytest.approx([.8, .8])
        assert result["participants"] == [True, True]
        assert result["physics"]["participantCount"] == 1
        assert result["physics"]["participatingMeshCount"] == 2
        assert result["hiddenPhysics"]["participantCount"] == 1
        assert result["sharedState"]
        assert result["sharedTransforms"]
        assert result["sharedRotation"]
        assert result["maxSeamError"] == pytest.approx(0)
        assert result["maxRevealError"] == pytest.approx(0)
    finally:
        context.close()


def test_selected_weight_topology_filters_weak_edges_and_pivots_synthetic_roots(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"WeightTopology": _payload("WeightTopology")})
    try:
        result = page.evaluate("""async () => {
          const weight = await import('./js/mesh/weight-experiment.js');
          const weak = {
            boneA: 7, boneB: 8, sharedVertexCount: 1,
            containment: .001, jaccard: .001, normalizedDistance: 0,
          };
          const candidateEdges = weight.candidateRelationshipEdges({
            relationships: [weak],
          });
          const tree = weight.buildMaximumSpanningTree(
            [{boneId: 7}, {boneId: 8}], candidateEdges);
          const forest = {
            components: [{
              rootId: -1, nodeIds: [-1, 7, 8],
              childrenById: {'-1': [7], '7': [8]},
            }],
          };
          const centers = new Map([
            [-1, [2, 0, 0]], [7, [2, 1, 0]], [8, [2, 2, 0]],
          ]);
          const transforms = weight.buildForestTransformsFromLocalRotations(
            forest, centers, {
              rotationByBoneId: new Map([[7, [0, 0, Math.PI / 2]]]),
            });
          const THREE = await import('three');
          const point = new THREE.Vector3(2, 1, 0).applyMatrix4(
            transforms.get(7));
          return {
            candidateCount: candidateEdges.length,
            componentCount: tree.components.length,
            pivotedPoint: point.toArray(),
          };
        }""")
        assert result == {
            "candidateCount": 0,
            "componentCount": 2,
            "pivotedPoint": pytest.approx([1, 0, 0]),
        }
    finally:
        context.close()


def test_selected_weight_topology_preserves_mirrored_branches_and_attachment(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"WeightTopologyMirrored": _payload("WeightTopologyMirrored")})
    try:
        result = page.evaluate("""async () => {
          const weight = await import('./js/mesh/weight-experiment.js');
          const edges = [
            {boneA: 45, boneB: 49, treeEdgeScore: .90,
              minOverlap: 90, containment: .90, jaccard: .30,
              normalizedDistance: .1},
            {boneA: 47, boneB: 53, treeEdgeScore: .89,
              minOverlap: 89, containment: .89, jaccard: .30,
              normalizedDistance: .1},
            {boneA: 45, boneB: 47, treeEdgeScore: .20,
              minOverlap: 2, containment: .20, jaccard: .02,
              normalizedDistance: .1},
          ];
          const staticAttachments = [
            {boneA: 2, boneB: 45, minOverlap: 120,
              sharedVertexCount: 300, containment: .40, jaccard: .20,
              normalizedDistance: .4},
            {boneA: 2, boneB: 47, minOverlap: 110,
              sharedVertexCount: 280, containment: .38, jaccard: .18,
              normalizedDistance: .4},
          ];
          const kept = weight.pruneSelectedRelationshipEdges(
            edges, [...edges, ...staticAttachments], [45, 49, 47, 53]);
          const chain = [
            {boneA: 10, boneB: 11, minOverlap: 90, treeEdgeScore: .90},
            {boneA: 11, boneB: 12, minOverlap: 60, treeEdgeScore: .60},
            {boneA: 12, boneB: 13, minOverlap: 95, treeEdgeScore: .95},
          ];
          const keptChain = weight.pruneSelectedRelationshipEdges(
            chain, chain, [10, 11, 12, 13]);
          const attachment = weight.selectAttachmentRelationship([
            {boneA: 2, boneB: 45, minOverlap: 120,
              sharedVertexCount: 300, containment: .40, jaccard: .20,
              normalizedDistance: .4},
            {boneA: 47, boneB: 58, minOverlap: 1,
              sharedVertexCount: 50, containment: 1, jaccard: .1,
              normalizedDistance: .1},
          ]);
          const left = weight.orientTree([
            {boneA: 2, boneB: 45}, {boneA: 45, boneB: 49}], 2);
          const right = weight.orientTree([
            {boneA: 2, boneB: 47}, {boneA: 47, boneB: 53}], 2);
          const forest = {components: [
            {rootId: 2, nodeIds: [2, 45, 49], childrenById: left.childrenById},
            {rootId: 2, nodeIds: [2, 47, 53], childrenById: right.childrenById},
          ]};
          const centers = new Map([
            [2, [0, 0, 0]], [45, [-1, 0, 0]], [49, [-1, 1, 0]],
            [47, [1, 0, 0]], [53, [1, 1, 0]],
          ]);
          const transforms = weight.buildForestTransformsFromLocalRotations(
            forest, centers, {rotationByBoneId: new Map([
                  [45, [0, 0, .2]], [47, [0, 0, -.2]],
                  [49, [0, 0, .15]], [53, [0, 0, -.15]],
            ])});
          const THREE = await import('three');
          const leftPoint = new THREE.Vector3(-1, 1, 0);
          const rightPoint = new THREE.Vector3(1, 1, 0);
          const leftMoved = leftPoint.clone().applyMatrix4(transforms.get(49))
            .distanceTo(leftPoint);
          const rightMoved = rightPoint.clone().applyMatrix4(transforms.get(53))
            .distanceTo(rightPoint);
          return {
            kept: kept.map(edge => `${edge.boneA}-${edge.boneB}`),
            keptChain: keptChain.map(edge => `${edge.boneA}-${edge.boneB}`),
            attachment: `${attachment.boneA}-${attachment.boneB}`,
            depths: [left.depthById[45], left.depthById[49],
              right.depthById[47], right.depthById[53]],
            moved: [leftMoved, rightMoved],
          };
        }""")
        assert result["kept"] == ["45-49", "47-53"]
        assert result["keptChain"] == ["10-11", "11-12", "12-13"]
        assert result["attachment"] == "2-45"
        assert result["depths"] == [1, 2, 1, 2]
        assert result["moved"][0] == pytest.approx(result["moved"][1])
    finally:
        context.close()


def test_model_bone_stats_sum_same_ids_before_averaging(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"WeightStats": _payload("WeightStats")})
    try:
        result = page.evaluate("""async () => {
          const weight = await import('./js/mesh/weight-experiment.js');
          return weight.aggregateModelBoneStats([
            [{boneId: 45, affectedVertexCount: 2, totalWeight: .6}],
            [
              {boneId: 45, affectedVertexCount: 4, totalWeight: 2},
              {boneId: 7, affectedVertexCount: 1, totalWeight: .25},
            ],
          ]);
        }""")
        assert result["45"] == {
            "affectedVertexCount": 6,
            "averageInfluence": pytest.approx(2.6 / 6),
        }
        assert result["7"] == {
            "affectedVertexCount": 1,
            "averageInfluence": pytest.approx(.25),
        }
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

def test_material_kind_refresh_hot_swaps_profile_without_reloading_model(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx")
    mesh_name, entry = next(iter(payload["meshes"].items()))
    entry["source"] = "Packed.ini"
    initial_semantic = copy.deepcopy(entry)
    initial_semantic.update({
        "source": "Packed.ini",
        "component": entry["component"],
        "material_kind": "body",
        "material_kind_reliable": False,
        "material_kind_reason": "automatic detection",
        "material_kind_override": None,
        "material_profile_id": "wuwa:rabbitfx",
    })
    explicit_semantic = copy.deepcopy(initial_semantic)
    explicit_semantic.update({
        "material_kind": "body",
        "material_kind_reliable": True,
        "material_kind_reason": "viewer material-kind override",
        "material_kind_override": "body",
        "material_profile_id": "wuwa:rabbitfx:body",
    })
    payload["meshSemantics"] = {mesh_name: initial_semantic}
    payload["metadata"]["material_profiles"]["wuwa:rabbitfx:body"] = \
        material_profile_for("wuwa", "rabbitfx", "body").to_metadata()

    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.locator(".draw-item").wait_for()
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial")
        page.locator("#inspector-tab").click()
        page.locator(".group-hdr .group-name").first.click()
        page.locator(".inspector-material-kind-control").wait_for()
        page.locator(".draw-item").first.click()
        page.evaluate("""async () => {
          const {setManualTexOverride} =
            await import('./js/mesh/mesh-state.js');
          const mesh = window.modViewer.activeMeshes[0];
          setManualTexOverride(mesh, 'diffuse::Packed-two.png', {
            notify: false,
          });
          document.querySelector('#wire-btn').click();
          document.querySelector('#shading-btn').click();
          document.querySelector('#glossy-btn').click();
          document.querySelector('#toon-btn').click();
          window.modViewer.setMaterialDebugMode('normal-data-b');
          const game = mesh.material.userData.gameMaterial;
          window.__materialHotSwapBefore = {
            mesh,
            geometry: mesh.geometry,
            position: mesh.position.toArray(),
            quaternion: mesh.quaternion.toArray(),
            positionAttribute: mesh.geometry.getAttribute('position'),
            shapeTargets: mesh.userData.shapeTargets,
            material: mesh.material,
            diffuseKey: mesh.userData.texKey,
            normalDataKey: mesh.userData.normalDataKey,
            manualTexOverride: mesh.userData.manualTexOverride,
            normalDataBound: game.bindings.normal_data.enabledNode.value,
            debugMode: window.modViewer.getMaterialState(0).debugMode,
            selected: game.selectionEnabledNode.value,
          };
        }""")
        page.evaluate("""data => {
          const response = window.__fakeApi.responses.Packed;
          window.__fakeApi.calls.materialKind = [];
          window.pywebview.api.save_component_material_kind =
            async (path, source, component, kind) => {
              window.__fakeApi.calls.materialKind.push(
                [path, source, component, kind]);
              response.meshSemantics = {
                [data.mesh]: kind === 'body'
                  ? data.explicit : data.automatic,
              };
              return {saved: true};
            };
        }""", {
            "mesh": mesh_name,
            "automatic": initial_semantic,
            "explicit": explicit_semantic,
        })

        page.locator(".inspector-material-kind-control").select_option("body")
        page.wait_for_function(
            "window.__fakeApi.calls.meshSemantics.length === 1")
        switched = page.evaluate("""() => {
          const before = window.__materialHotSwapBefore;
          const mesh = window.modViewer.activeMeshes[0];
          const game = mesh.material.userData.gameMaterial;
          return {
            sameMesh: mesh === before.mesh,
            sameGeometry: mesh.geometry === before.geometry,
            samePositionAttribute:
              mesh.geometry.getAttribute('position') === before.positionAttribute,
            sameShapeTargets: mesh.userData.shapeTargets === before.shapeTargets,
            position: mesh.position.toArray(),
            quaternion: mesh.quaternion.toArray(),
            newMaterial: mesh.material !== before.material,
            profileId: mesh.userData.materialProfileId,
            materialKindOverride: mesh.userData.materialKindOverride,
            manualTexOverride: mesh.userData.manualTexOverride,
            diffuseKey: mesh.userData.texKey,
            normalDataKey: mesh.userData.normalDataKey,
            normalDataBound: game.bindings.normal_data.enabledNode.value,
            normalSource: game.normalSource,
            debugMode: window.modViewer.getMaterialState(0).debugMode,
            selected: game.selectionEnabledNode.value,
            wireframe: mesh.material.wireframe,
            flatShading: mesh.material.flatShading,
            roughness: mesh.material.roughness,
            toon: game.toonEnabledNode.value,
            rim: game.rimEnabledNode.value,
          };
        }""")
        assert switched["sameMesh"]
        assert switched["sameGeometry"]
        assert switched["samePositionAttribute"]
        assert switched["sameShapeTargets"]
        assert switched["position"] == pytest.approx(
            page.evaluate("window.__materialHotSwapBefore.position"))
        assert switched["quaternion"] == pytest.approx(
            page.evaluate("window.__materialHotSwapBefore.quaternion"))
        assert switched["newMaterial"]
        assert switched["profileId"] == "wuwa:rabbitfx:body"
        assert switched["materialKindOverride"] == "body"
        assert switched["manualTexOverride"] == "diffuse::Packed-two.png"
        assert switched["diffuseKey"] == "diffuse::Packed-two.png"
        assert switched["normalDataKey"] == initial_semantic["normal_data_key"]
        assert switched["normalDataBound"]
        assert switched["normalSource"] == "normal_data"
        assert switched["debugMode"] == "normal-data-b"
        assert switched["selected"]
        assert switched["wireframe"]
        assert switched["flatShading"]
        assert switched["roughness"] == pytest.approx(0.2)
        assert switched["toon"]
        assert not switched["rim"]
        assert page.evaluate("window.__fakeApi.calls.loadMod") == ["Packed"]
        assert page.evaluate("window.__fakeApi.calls.materialKind") == [[
            "Packed", "Packed.ini", "Body Packed", "body"]]

        page.locator(".inspector-material-kind-control").select_option("auto")
        page.wait_for_function(
            "window.__fakeApi.calls.meshSemantics.length === 2")
        automatic = page.evaluate("""() => {
          const mesh = window.modViewer.activeMeshes[0];
          const game = mesh.material.userData.gameMaterial;
          return {
            sameMesh: mesh === window.__materialHotSwapBefore.mesh,
            sameGeometry: mesh.geometry ===
              window.__materialHotSwapBefore.geometry,
            profileId: mesh.userData.materialProfileId,
            materialKindOverride: mesh.userData.materialKindOverride,
            manualTexOverride: mesh.userData.manualTexOverride,
            debugMode: window.modViewer.getMaterialState(0).debugMode,
            selected: game.selectionEnabledNode.value,
            normalDataBound: game.bindings.normal_data.enabledNode.value,
            wireframe: mesh.material.wireframe,
            flatShading: mesh.material.flatShading,
            roughness: mesh.material.roughness,
            toon: game.toonEnabledNode.value,
          };
        }""")
        assert automatic == {
            "sameMesh": True,
            "sameGeometry": True,
            "profileId": "wuwa:rabbitfx",
            "materialKindOverride": None,
            "manualTexOverride": "diffuse::Packed-two.png",
            "debugMode": "normal-data-b",
            "selected": True,
            "normalDataBound": True,
            "wireframe": True,
            "flatShading": True,
            "roughness": pytest.approx(0.2),
            "toon": True,
        }
        assert page.evaluate("window.__fakeApi.calls.loadMod") == ["Packed"]
        assert page.evaluate("window.__fakeApi.calls.materialKind") == [
            ["Packed", "Packed.ini", "Body Packed", "body"],
            ["Packed", "Packed.ini", "Body Packed", "auto"],
        ]
    finally:
        context.close()


def _skinning_material_transition_payload():
    payload = _packed_material_payload("wuwa:rabbitfx")
    mesh_name, entry = next(iter(payload["meshes"].items()))
    entry["source"] = "Packed.ini"
    initial_semantic = copy.deepcopy(entry)
    initial_semantic.update({
        "source": "Packed.ini",
        "component": entry["component"],
        "material_kind": "body",
        "material_kind_reliable": False,
        "material_kind_reason": "automatic detection",
        "material_kind_override": None,
        "material_profile_id": "wuwa:rabbitfx",
    })
    explicit_semantic = copy.deepcopy(initial_semantic)
    explicit_semantic.update({
        "material_kind": "body",
        "material_kind_reliable": True,
        "material_kind_reason": "viewer material-kind override",
        "material_kind_override": "body",
        "material_profile_id": "wuwa:rabbitfx:body",
    })
    payload["meshSemantics"] = {mesh_name: initial_semantic}
    payload["metadata"]["material_profiles"]["wuwa:rabbitfx:body"] = \
        material_profile_for("wuwa", "rabbitfx", "body").to_metadata()
    return payload, mesh_name, explicit_semantic


def test_material_hot_swap_updates_loaded_skinning_baseline(
        edge_browser, frontend_url):
    payload, mesh_name, explicit_semantic = \
        _skinning_material_transition_payload()
    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial")
        page.evaluate("""data => {
          window.__fakeApi.responses.Packed.meshSemantics = {
            [data.mesh]: data.semantic,
          };
        }""", {"mesh": mesh_name, "semantic": explicit_semantic})
        result = page.evaluate("""async () => {
          const mesh = window.modViewer.activeMeshes[0];
          const bytes = new Uint8Array(48);
          new Uint32Array(bytes.buffer).set([0, 1, 1, 2, 0, 2]);
          new Float32Array(bytes.buffer, 24).set([.8, .2, .7, .3, .6, .4]);
          const url = URL.createObjectURL(new Blob([bytes]));
          window.pywebview.api.get_skinning_preview = async () => ({
            status: 'ok', vertex_count: 3, influence_count: 2,
            bone_ids: [0, 1, 2], encoding: 'test', source: {
              key: 'test/bodyblend.buf|offset=0', file: 'Test/BodyBlend.buf',
              bone_id_offset: 0,
            },
            data: {
              url, length: 48,
              indices: {offset: 0, length: 24, type: 'u32'},
              weights: {offset: 24, length: 24, type: 'f32'},
            }, diagnostics: {},
          });
          const experiment = await import('./js/mesh/weight-experiment.js');
          const {setControlValue} =
            await import('./js/editing/control-state.js');
          const {refreshMeshes} = await import('./js/mesh/mesh-state.js');
          await experiment.loadSkinningWeights(mesh);
          const oldMaterial = mesh.material;
          let oldMaterialDisposals = 0;
          oldMaterial.addEventListener('dispose',
            () => oldMaterialDisposals += 1);
          const refreshed = await window.modViewer.refreshMeshSemantics();
          const afterSwap = experiment.getSkinningState(mesh);
          const newMaterial = afterSwap.originalMaterial;
          setControlValue('shape', '1');
          refreshMeshes();
          URL.revokeObjectURL(url);
          return {
            refreshed,
            oldMaterialDisposals,
            newProfile: newMaterial.userData.gameMaterial.profile.id,
            originalTracksNew: afterSwap.originalMaterial === newMaterial,
            stateDisposed: experiment.getSkinningState(mesh) === null,
            activeMaterialIsNew: mesh.material === newMaterial,
            activeProfile: mesh.material.userData.gameMaterial.profile.id,
          };
        }""")
        assert result == {
            "refreshed": True,
            "oldMaterialDisposals": 1,
            "newProfile": "wuwa:rabbitfx:body",
            "originalTracksNew": True,
            "stateDisposed": False,
            "activeMaterialIsNew": True,
            "activeProfile": "wuwa:rabbitfx:body",
        }
    finally:
        context.close()


def test_material_hot_swap_preserves_active_skinning_heatmap(
        edge_browser, frontend_url):
    payload, mesh_name, explicit_semantic = \
        _skinning_material_transition_payload()
    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial")
        page.evaluate("""data => {
          window.__fakeApi.responses.Packed.meshSemantics = {
            [data.mesh]: data.semantic,
          };
        }""", {"mesh": mesh_name, "semantic": explicit_semantic})
        result = page.evaluate("""async () => {
          const mesh = window.modViewer.activeMeshes[0];
          const bytes = new Uint8Array(48);
          new Uint32Array(bytes.buffer).set([0, 1, 1, 2, 0, 2]);
          new Float32Array(bytes.buffer, 24).set([.8, .2, .7, .3, .6, .4]);
          const url = URL.createObjectURL(new Blob([bytes]));
          window.pywebview.api.get_skinning_preview = async () => ({
            status: 'ok', vertex_count: 3, influence_count: 2,
            bone_ids: [0, 1, 2], encoding: 'test', source: {
              key: 'test/bodyblend.buf|offset=0', file: 'Test/BodyBlend.buf',
              bone_id_offset: 0,
            },
            data: {
              url, length: 48,
              indices: {offset: 0, length: 24, type: 'u32'},
              weights: {offset: 24, length: 24, type: 'f32'},
            }, diagnostics: {},
          });
          const experiment = await import('./js/mesh/weight-experiment.js');
          await experiment.loadSkinningWeights(mesh);
          experiment.setSelectedBones([{
            sourceKey: 'test/bodyblend.buf|offset=0',
            sourceFile: 'Test/BodyBlend.buf', boneIdOffset: 0, boneIds: [1],
          }]);
          const oldMaterial = mesh.material;
          let oldMaterialDisposals = 0;
          oldMaterial.addEventListener('dispose',
            () => oldMaterialDisposals += 1);
          experiment.setSkinningHeatmap(mesh, true);
          const heatmapMaterial = mesh.material;
          let heatmapDisposals = 0;
          heatmapMaterial.addEventListener('dispose',
            () => heatmapDisposals += 1);
          const refreshed = await window.modViewer.refreshMeshSemantics();
          const afterSwap = experiment.getSkinningState(mesh);
          const newMaterial = afterSwap.originalMaterial;
          const displayedAfterSwap = mesh.material === heatmapMaterial;
          experiment.setSelectedBone(mesh, 1);
          const selectedBoneKeepsHeatmap =
            mesh.material === heatmapMaterial
            && experiment.getSkinningState(mesh).debugMaterial === heatmapMaterial;
          const disabled = experiment.setSkinningHeatmap(mesh, false);
          URL.revokeObjectURL(url);
          return {
            refreshed,
            oldMaterialDisposals,
            heatmapDisposals,
            displayedAfterSwap,
            selectedBoneKeepsHeatmap,
            originalTracksNew: afterSwap.originalMaterial === newMaterial,
            newProfile: newMaterial.userData.gameMaterial.profile.id,
            disabled,
            restoredAfterDisable: mesh.material === newMaterial,
            heatmapCleared: afterSwap.debugMaterial === null
              && afterSwap.heatmapMode === null,
            activeProfile: mesh.material.userData.gameMaterial.profile.id,
          };
        }""")
        assert result == {
            "refreshed": True,
            "oldMaterialDisposals": 1,
            "heatmapDisposals": 1,
            "displayedAfterSwap": True,
            "selectedBoneKeepsHeatmap": True,
            "originalTracksNew": True,
            "newProfile": "wuwa:rabbitfx:body",
            "disabled": False,
            "restoredAfterDisable": True,
            "heatmapCleared": True,
            "activeProfile": "wuwa:rabbitfx:body",
        }
    finally:
        context.close()


def test_material_kind_control_reverts_when_semantic_refresh_fails(
        edge_browser, frontend_url):
    payload = _packed_material_payload("wuwa:rabbitfx")
    context, page = _page(edge_browser, frontend_url, {"Packed": payload})
    try:
        _open(page, "Packed")
        page.wait_for_function(
            "window.modViewer.activeMeshes[0]?.material?.userData?.gameMaterial")
        page.locator("#inspector-tab").click()
        page.locator(".group-hdr .group-name").first.click()
        select = page.locator(".inspector-material-kind-control")
        select.wait_for()
        assert select.input_value() == "auto"
        page.evaluate("""() => {
          window.pywebview.api.save_component_material_kind =
            async () => ({saved: true});
          window.pywebview.api.get_mesh_semantics =
            async () => ({error: 'semantic refresh failed'});
        }""")

        select.select_option("body")
        page.locator("#dialog-backdrop.show").wait_for()
        page.locator("#dialog-ok").click()
        page.wait_for_function(
            "() => document.querySelector('.inspector-material-kind-control')"
            "?.value === 'auto'")
        assert page.evaluate("window.modViewer.getMaterialState(0).profileId") == (
            "wuwa:rabbitfx")
        assert page.evaluate(
            "window.modViewer.getMaterialState(0).materialKindOverride") is None
        assert page.evaluate("window.__fakeApi.calls.loadMod") == ["Packed"]
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
