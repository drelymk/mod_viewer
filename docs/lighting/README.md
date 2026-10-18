# Environment lighting comparison

The production change is limited to the existing preset parameters in
`src/web/js/scene/environment.js`. The movable key remains at its existing
default intensity of 1; Default lighting remains the reference. Materials,
shadow rendering, sky direction, capture scenes, PMREM generation and caching,
tone mapping and viewport exposure retain their existing implementations.

## Calibration

| Preset | Reason for the adjustment |
| --- | --- |
| Studio | Increase broad neutral IBL to lift shadow-facing fabric and faces. Reduce the directional accent so it contributes less of a second toon boundary alongside the movable key. |
| Indoor | Retain warm room reflections and bounce, reduce uniform fill slightly, and strengthen the existing cool directional accent. Keep it dimmer and more directional than Studio. |
| Outdoor | Exchange some saturated sky IBL for brighter, less saturated ambient and hemisphere fill. Keep sunlight intensity and direction unchanged, protecting lit whites while lifting the shadow side. |
| Sunset | Modestly lift IBL and indirect fill and reduce its purple cast. Retain the low warm sun and directional contrast. |

## Matched screenshots

The baseline is commit `3fe53fe`, following PR #182. Each sheet shows Genshin,
ZZZ and WuWa mod geometry, with identical camera and materials within each
before/after pair. Columns are before/after with the key off, followed by
before/after at intensity 1. Images are cropped from the viewer output without
color adjustment. Rest geometry is used to avoid time-dependent animation.
These are visual comparisons, not universal reference images for every mod:
authored texture bindings and missing original parts remain model-dependent.

- [Studio](studio.png)
- [Indoor](indoor.png)
- [Outdoor](outdoor.png)
- [Sunset](sunset.png)
- [Synthetic material comparison](materials.png), including reflective metal,
  white diffuse, toon and alpha-tested surfaces at key intensity 1.

## Reproduce with another model

Save the baseline environment module before modifying it, then run the same
command with and without `--source baseline-environment.js`, choosing separate
output folders:

```text
python tools/benchmark_environments.py --mod MODEL_FOLDER --capture-only --repeats 1 --presets default studio indoor outdoor sunset --source baseline-environment.js --output .quality-reports/environment-before
python tools/benchmark_environments.py --mod MODEL_FOLDER --capture-only --repeats 1 --presets default studio indoor outdoor sunset --output .quality-reports/environment-after
```

The tool only reads the mod. Add `--disabled-ini` to select disabled INIs without
renaming or enabling them. Camera settings and mesh counts are saved in the
report; compare them before assessing the images. The key is captured at 0 and
1 for every preset. Default provides an unchanged reference for both settings.

Omit `--capture-only` for the existing AO/bloom comparisons, camera-motion
timings and repeated cached switches. Omit `--mod` for the synthetic material
set, which includes white diffuse, metal, rough and glossy surfaces, toon,
transparency, emissive and alpha-tested hair. Use repeated runs under the same
viewport and GPU conditions; timings include browser scheduling and GPU queue
completion. Check `cachedMemoryBefore`, `cachedMemoryAfter` and
`cachedEnvironment` alongside frame timings. Cached switches should not
generate additional PMREMs.

The existing local browser tests cover background independence, restoration of
Default, sun alignment, cache reuse and disposal, key-light independence, and
drag/capture-loss behavior. They remain excluded from GitHub Actions.
