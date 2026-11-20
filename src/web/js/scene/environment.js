// Viewer-only environment moods. This module deliberately knows nothing
// about mods, meshes, INIs, or the Python bridge.

import * as THREE from 'three/webgpu';
import {
  Fn,
  cameraPosition,
  clamp,
  color,
  exp,
  float,
  floor as cellOf,
  fract,
  fwidth,
  length,
  max,
  min,
  mix,
  mx_cell_noise_float,
  mx_fractal_noise_float,
  mx_noise_float,
  mx_worley_noise_float_2d,
  neutralToneMapping,
  normalize,
  normalWorldGeometry,
  smoothstep,
  step,
  uniform,
  vec2,
  vec3,
  vec4,
  varyingProperty,
} from 'three/tsl';
import { SkyMesh } from 'three/addons/objects/SkyMesh.js';

const freeze = Object.freeze;

function deepFreeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.values(value).forEach(deepFreeze);
    freeze(value);
  }
  return value;
}

function makeAtmosphere(sunElevation, sunAzimuth, sky, exposure, groundColor) {
  const direction = new THREE.Vector3().setFromSphericalCoords(
    1,
    THREE.MathUtils.degToRad(90 - sunElevation),
    THREE.MathUtils.degToRad(sunAzimuth),
  );
  const daylight = THREE.MathUtils.smoothstep(direction.y, 0, 0.7);
  const { cloudCoverage, cloudDensity, cloudElevation, cloudScale, ...scattering } = sky;
  return deepFreeze({
    sky: scattering,
    clouds: { coverage: cloudCoverage, density: cloudDensity, elevation: cloudElevation, scale: cloudScale },
    sunDirection: direction.toArray(),
    sunColor: new THREE.Color(0xffa35c).lerp(new THREE.Color(0xfff2df), daylight).getHex(),
    sunIntensity: THREE.MathUtils.lerp(0.35, 0.7, daylight),
    exposure,
    groundColor,
  });
}

const OUTDOOR_ATMOSPHERE = makeAtmosphere(
  50,
  -45,
  {
    turbidity: 2.5,
    rayleigh: 0.8,
    mieCoefficient: 0.003,
    mieDirectionalG: 0.75,
    cloudCoverage: 0.4,
    cloudDensity: 0.65,
    cloudElevation: 0.5,
    cloudScale: 0.0004,
  },
  0.25,
  0x6b5a47,
);
const SUNSET_ATMOSPHERE = makeAtmosphere(
  5,
  -160,
  {
    turbidity: 6,
    rayleigh: 2.5,
    mieCoefficient: 0.006,
    mieDirectionalG: 0.82,
    cloudCoverage: 0.43,
    cloudDensity: 0.6,
    cloudElevation: 0.45,
    cloudScale: 0.0004,
  },
  0.5,
  0x574849,
);

const INDOOR_DAYLIGHT = 0xd8eaff;
const atmosphereAccent = (atmosphere) => ({
  color: atmosphere.sunColor,
  intensity: atmosphere.sunIntensity,
  position: atmosphere.sunDirection,
});

/**
 * One definition per preset.
 *
 * - palette: one colour script for the backdrop, the background floor and the
 *   derived ambient/hemisphere tints, so models pick up the tint shown behind
 *   them. Atmosphere zenith/horizon colours approximate the scattering sky.
 * - accent: preset light, positioned relative to the model target.
 * - lights: baseline intensities until the IBL capture is ready (or if it fails).
 * - ibl: environment intensity and the lighter fill used once IBL is active.
 * - floor: in model-size units, so tiny and huge mods share one composition.
 *   `surface` picks the procedural material (polished tile, varnished wood
 *   boards or packed dirt); `soak` darkens it where puddles wet it.
 * - capture: the room baked into PMREM for indoor presets. Dark capture walls
 *   leave metals and shadow-facing surfaces almost black, so rooms carry broad
 *   bounce as well as softbox highlights.
 */
const PRESET_DEFINITIONS = deepFreeze({
  studio: {
    palette: {
      zenith: 0xa3a3a3,
      horizonToward: 0xaca59b,
      horizonAway: 0x959aa1,
      floor: 0x7e7e7e,
      sun: 0xffffff,
      bounce: 0.24,
    },
    accent: { color: 0xffffff, intensity: 0.08, position: [-4, 6, 5] },
    lights: { ambient: 0.32, hemisphere: 0.36 },
    // Broad neutral IBL lifts faces and fabric without adding a competing
    // toon boundary beside the movable inspection key.
    ibl: { intensity: 0.85, ambient: 0.1, hemisphere: 0.23 },
    backdrop: { spill: 0.22, daylight: 0 },
    floor: { surface: 'tile', base: 0.42, pool: 0.75, poolRadius: 0.9, gloss: 0.3, puddles: 0, haze: 2.6, glint: 0 },
    capture: {
      room: { color: 0xa6a6a6, size: [18, 12, 18], y: 1 },
      floorY: -4.95,
      mainCard: { intensity: 1.7, distance: 6, size: [7.0, 5.5] },
      cards: [
        { color: 0xf5f7fa, intensity: 0.8, position: [5.0, 2.2, 3.5], size: [4.5, 5.5] },
        { color: 0xffffff, intensity: 0.7, position: [0, 4.8, 0], size: [7.0, 4.0] },
        { color: 0xf7f7f7, intensity: 1.25, position: [0, 2.5, -5.5], size: [6.0, 3.0], target: [0, 1, 0] },
      ],
      roomLight: { color: 0xffffff, distance: 2.0 },
    },
  },
  indoor: {
    palette: {
      zenith: 0x5e4b3b,
      horizonToward: 0x74767d,
      horizonAway: 0x735a46,
      floor: 0x866044,
      sun: INDOOR_DAYLIGHT,
      bounce: 0.48,
    },
    accent: { color: INDOOR_DAYLIGHT, intensity: 0.5, position: [-5, 2.5, -7] },
    lights: { ambient: 0.24, hemisphere: 0.32 },
    ibl: { intensity: 0.65, ambient: 0.18, hemisphere: 0.26 },
    backdrop: { spill: 0, daylight: 0.04 },
    floor: {
      surface: 'wood',
      base: 0.5,
      pool: 0.55,
      poolRadius: 1.0,
      gloss: 0.18,
      puddles: 0.12,
      patch: 0.9,
      haze: 3.2,
      glint: 0,
    },
    capture: {
      room: { color: 0xb99a7b, size: [18, 10, 18], y: 2 },
      floorY: -2.95,
      mainCard: { intensity: 3.0, distance: 8, size: [3.8, 3.2] },
      cards: [
        { color: 0xffe2bf, intensity: 0.75, position: [0, 6.5, 0], size: [7.0, 5.0] },
        { color: 0xffc58f, intensity: 1.3, position: [5.0, 1.0, 1.5], size: [6.0, 4.5] },
      ],
      roomLight: { color: 0xffd6ab, distance: 1.5 },
    },
  },
  outdoor: {
    atmosphere: OUTDOOR_ATMOSPHERE,
    palette: {
      zenith: 0x5a8ccc,
      horizonToward: 0xe6dfcf,
      horizonAway: 0xb9cde0,
      floor: OUTDOOR_ATMOSPHERE.groundColor,
      sun: OUTDOOR_ATMOSPHERE.sunColor,
      bounce: 0.29,
    },
    accent: atmosphereAccent(OUTDOOR_ATMOSPHERE),
    lights: { ambient: 0.36, hemisphere: 0.55 },
    // Keep the blue sky in reflections, with less saturated indirect fill
    // carrying shadow-facing skin and white fabric instead of extra sunlight.
    ibl: { intensity: 1.0, ambient: 0.22, hemisphere: 0.38 },
    floor: {
      surface: 'dirt',
      base: 0.9,
      pool: 0.12,
      poolRadius: 1.2,
      gloss: 0.04,
      puddles: 0.35,
      soak: 0.45,
      patch: 2.4,
      haze: 5,
      glint: 1,
    },
  },
  sunset: {
    atmosphere: SUNSET_ATMOSPHERE,
    palette: {
      zenith: 0x34405f,
      horizonToward: 0xf09a5e,
      horizonAway: 0x6b6683,
      floor: SUNSET_ATMOSPHERE.groundColor,
      sun: SUNSET_ATMOSPHERE.sunColor,
      bounce: 0.26,
    },
    accent: atmosphereAccent(SUNSET_ATMOSPHERE),
    lights: { ambient: 0.24, hemisphere: 0.44 },
    ibl: { intensity: 0.8, ambient: 0.14, hemisphere: 0.26 },
    floor: {
      surface: 'dirt',
      base: 0.85,
      pool: 0.18,
      poolRadius: 1.2,
      gloss: 0.06,
      puddles: 0.45,
      soak: 0.45,
      patch: 2.4,
      haze: 5,
      glint: 2,
    },
  },
});

const PMREM = freeze({ size: 256, sigma: 0, near: 0.1, far: 100 });
const CAPTURE_ROOM_LIGHT_INTENSITY = 50;
const DEFAULT_FLOOR_SCALE = 2;
// Saltline-style specular clamp: a low sun on a mirror patch must not produce
// single-pixel fireflies or overwhelm bloom.
const MAX_GLINT = 4;
const GLINT_POWER = 300;
const SKY_TINT = 0.35;
const GROUND_TINT = 0.65;
const AMBIENT_TINT = 0.2;

function tintTowards(base, strength) {
  const peak = Math.max(base.r, base.g, base.b, 1e-6);
  return new THREE.Color(1, 1, 1).lerp(base.clone().multiplyScalar(1 / peak), strength);
}

// Ambient sky light weights the zenith over both horizons; ground bounce is
// the floor albedo lit by an even mix of sun and sky. Only hue and saturation
// are derived, so per-preset light intensities keep their tuned values.
function deriveLightColors(palette) {
  const linear = (hex) => new THREE.Color(hex);
  const skyLight = linear(palette.zenith)
    .multiplyScalar(0.6)
    .add(linear(palette.horizonAway).multiplyScalar(0.5))
    .add(linear(palette.horizonToward).multiplyScalar(0.25));
  const incoming = tintTowards(linear(palette.sun), 1).lerp(tintTowards(skyLight, 1), 0.5);
  const bounce = linear(palette.floor).multiply(incoming);
  const ambient = tintTowards(skyLight, 1).lerp(tintTowards(bounce, 1), 0.35);
  return {
    ambient: tintTowards(ambient, AMBIENT_TINT).getHex(),
    sky: tintTowards(skyLight, SKY_TINT).getHex(),
    ground: tintTowards(bounce, GROUND_TINT).multiplyScalar(palette.bounce).getHex(),
  };
}

// Keep the visual vocabulary in one declarative table.
export const ENVIRONMENT_PRESETS = deepFreeze({
  default: { id: 'default' },
  ...Object.fromEntries(
    Object.entries(PRESET_DEFINITIONS).map(([id, definition]) => [
      id,
      {
        id,
        ...definition,
        lightColors: deriveLightColors(definition.palette),
        // Atmosphere sun directions are already unit vectors and stay exact.
        lightDirection:
          definition.atmosphere?.sunDirection ??
          new THREE.Vector3().fromArray(definition.accent.position).normalize().toArray(),
      },
    ]),
  ),
});

// Horizontal alignment with the dominant light, sharpened like Saltline's
// sun-facing haze: warm toward the light, cool away from it.
function horizonColor(direction, palette, lightDirection) {
  const flatLight = new THREE.Vector2(lightDirection[0], lightDirection[2]);
  if (flatLight.lengthSq() < 1e-10) flatLight.set(1, 0);
  flatLight.normalize();
  const flat = vec2(direction.x, direction.z).add(vec2(1e-5, 0)).normalize();
  const toward = max(0, flat.dot(vec2(flatLight.x, flatLight.y))).pow(3);
  return mix(color(palette.horizonAway), color(palette.horizonToward), toward);
}

function skyGradient(direction, palette, lightDirection) {
  return mix(horizonColor(direction, palette, lightDirection), color(palette.zenith), smoothstep(0, 0.85, direction.y));
}

// Room backdrops are pure functions of direction so the floor can reuse them
// for mirrored reflections. Both halves meet at the horizon colour.
function createRoomBackdrop({ palette, lightDirection, backdrop: { spill, daylight } }) {
  const light = vec3(...lightDirection);
  return Fn(([direction]) => {
    const lower = mix(
      color(palette.floor).mul(0.32),
      horizonColor(direction, palette, lightDirection),
      smoothstep(-0.5, 0, direction.y),
    );
    const facing = direction.dot(light);
    const keySpill = max(0, facing)
      .pow(4)
      .mul(spill)
      .add(1 - spill);
    const daylightWash = color(palette.sun).mul(smoothstep(-0.4, 1, facing).mul(daylight));
    return mix(lower, skyGradient(direction, palette, lightDirection), step(0, direction.y))
      .mul(keySpill)
      .add(daylightWash);
  });
}

/*
 * Procedural floor surfaces, in the same model-size units as the rest of the
 * floor (one unit is roughly the model's rest-pose diagonal, so a character
 * stands on boards about a hand wide). Each returns an albedo multiplier that
 * averages near one, so the palette floor colour, and the ambient tints derived
 * from it, still describe the floor as a whole. `gloss` scales the preset
 * gloss and `slope` is the surface gradient along world x/z, used to light
 * relief and to bend reflections. Every feature fades to its average once the
 * pixel footprint approaches its size, so distant floor never shimmers.
 */

// Visibility of a feature with the given repeat, from the pixel footprint.
function resolved(footprint, period) {
  return smoothstep(period * 0.15, period * 0.6, footprint).oneMinus();
}

// Box-filtered coverage of a seam (half-width `half`) at distance `d`; once
// the footprint spans the repeat it settles to the seam's area fraction.
function seamCoverage(d, half, period, footprint) {
  const sharp = clamp(footprint.mul(0.5).add(half).sub(d).div(footprint), 0, 1);
  return mix(sharp, (2 * half) / period, smoothstep(period * 0.1, period * 0.4, footprint));
}

const TILE = freeze({ size: 0.27, grout: 0.0016, bevel: 0.004 });

// Large-format polished porcelain with soft stone clouding and thin grout.
function tileSurface({ point, footprint }) {
  const grid = point.div(TILE.size);
  const cell = cellOf(grid);
  const within = fract(grid);
  const id = mx_cell_noise_float(cell).toVar();
  const id2 = mx_cell_noise_float(cell.add(vec2(57, 13))).toVar();
  const edge = min(min(within.x, within.x.oneMinus()), min(within.y, within.y.oneMinus())).mul(TILE.size);
  const grout = seamCoverage(edge, TILE.grout, TILE.size, footprint).toVar();
  const seed = id.mul(13);
  const clouding = mx_noise_float(vec3(point.mul(4), seed)).mul(0.05);
  // Soft, low-contrast veining; sharp veins read as cracks at viewer scale.
  const veins = smoothstep(0, 0.2, mx_fractal_noise_float(vec3(point.mul(5), seed), 3, 2, 0.5).abs())
    .oneMinus()
    .mul(resolved(footprint, 0.03));
  const speckle = mx_cell_noise_float(point.mul(700)).sub(0.5).mul(0.08).mul(resolved(footprint, 0.0015));
  // Rounded tile edges catch less light just before the grout.
  const bevel = smoothstep(TILE.grout, TILE.grout + TILE.bevel, edge)
    .mul(0.1)
    .add(0.9);
  const tone = mix(0.95, 1.06, id)
    .mul(clouding.add(1))
    .mul(veins.mul(-0.05).add(1))
    .add(speckle)
    .mul(mix(1, bevel, resolved(footprint, TILE.bevel * 2)));
  const smudges = mx_noise_float(vec3(point.mul(3), 9))
    .mul(0.25)
    .add(0.95);
  return {
    albedo: mix(vec3(tone), vec3(0.56, 0.56, 0.58), grout),
    gloss: mix(0.85, 1.15, id2).mul(smudges).mul(grout.oneMinus()),
    // Lippage: each tile sits a hair off level, breaking reflections per tile.
    slope: vec2(id.sub(0.5), id2.sub(0.5)).mul(0.012).mul(grout.oneMinus()),
  };
}

const PLANK = freeze({ width: 0.065, length: 0.6, gap: 0.0009, rings: 5 });

// Varnished oak boards running away from the default front camera, with
// staggered end joints, per-board tone, growth rings and fibre streaks.
function woodSurface({ point, footprint }) {
  const across = point.x.div(PLANK.width);
  const row = cellOf(across);
  const along = point.y.div(PLANK.length).add(mx_cell_noise_float(vec2(row, 17.5)));
  const board = cellOf(along);
  const id = mx_cell_noise_float(vec2(row, board)).toVar();
  const id2 = mx_cell_noise_float(vec2(board.add(31), row)).toVar();
  const u = fract(along);
  const v = fract(across).toVar();
  const side = min(v, v.oneMinus()).mul(PLANK.width);
  const end = min(u, u.oneMinus()).mul(PLANK.length);
  const gaps = max(
    seamCoverage(side, PLANK.gap, PLANK.width, footprint),
    seamCoverage(end, PLANK.gap, PLANK.length, footprint),
  ).toVar();
  const seed = id.mul(97);
  // Gentle, uneven drift keeps grain near-straight along the board.
  const wobble = mx_fractal_noise_float(vec3(point.y.mul(3), v.mul(2), seed), 2, 2.3, 0.4);
  const rings = fract(v.mul(PLANK.rings).add(wobble.mul(0.45)).add(id2.mul(5)));
  const lateWood = smoothstep(0.55, 0.7, rings)
    .mul(smoothstep(0.82, 1, rings).oneMinus())
    .mul(resolved(footprint, PLANK.width / PLANK.rings));
  const fibres = mx_noise_float(vec3(point.y.mul(18), point.x.mul(420), seed)).mul(resolved(footprint, 0.0025));
  const streaks = mx_noise_float(vec3(point.y.mul(4), point.x.mul(90), seed.add(5))).mul(resolved(footprint, 0.011));
  const wear = mx_noise_float(vec3(point.mul(2), 3))
    .mul(0.06)
    .add(1);
  const warmth = mix(vec3(1.05, 0.98, 0.9), vec3(0.95, 1.0, 1.07), id2);
  const tone = mix(0.8, 1.18, id)
    .mul(lateWood.mul(-0.14).add(1))
    .mul(fibres.mul(0.06).add(1))
    .mul(streaks.mul(0.08).add(1))
    .mul(wear)
    .mul(gaps.mul(-0.7).add(1));
  return {
    albedo: warmth.mul(tone),
    gloss: mix(0.7, 1.2, id2).mul(gaps.oneMinus()),
    // Boards cup slightly across their width, so varnish reflections break
    // board by board instead of mirroring one flat sheet.
    slope: vec2(v.sub(0.5).mul(0.05).mul(resolved(footprint, PLANK.width)), id.sub(0.5).mul(0.02)),
  };
}

const DIRT = freeze({ clods: 16, stones: 30, relief: 0.002 });

function dirtRelief(point) {
  const clods = mx_fractal_noise_float(vec3(point.mul(DIRT.clods), 0), 4, 2.1, 0.5);
  // Stones vary in size and thin out unevenly instead of forming clusters of
  // identical discs; most Worley cells hold none. Half jitter keeps every
  // feature point within [0.25, 0.75] of its cell, so a disc no wider than
  // 0.24 never crosses into a neighbour and per-cell size and presence agree.
  const stoneCell = point.mul(DIRT.stones);
  const size = mx_cell_noise_float(stoneCell.add(vec2(0.5, 9.5)));
  const present = step(0.55, mx_cell_noise_float(stoneCell));
  const scatter = smoothstep(-0.4, 0.4, mx_noise_float(vec3(point.mul(3), 4)));
  const radius = mix(0.07, 0.23, size);
  const stones = smoothstep(radius.mul(0.6), radius, mx_worley_noise_float_2d(stoneCell, 0.5))
    .oneMinus()
    .mul(present)
    .mul(scatter);
  return { clods, stones, height: clods.mul(0.003).add(stones.mul(0.002)) };
}

// Packed earth with clods, scattered pebbles, sandy grain and dry grass,
// relief-lit from the preset's dominant light.
function dirtSurface({ point, footprint, lightDirection }) {
  const here = dirtRelief(point);
  const clods = here.clods.toVar();
  const stones = here.stones.mul(resolved(footprint, 1 / DIRT.stones)).toVar();
  // One directional difference toward the light is enough to shade relief.
  const flat = new THREE.Vector2(lightDirection[0], lightDirection[2]);
  if (flat.lengthSq() < 1e-10) flat.set(1, 0);
  flat.normalize();
  const towardLight = vec2(flat.x, flat.y);
  const rise = here.height.sub(dirtRelief(point.sub(towardLight.mul(DIRT.relief))).height).div(DIRT.relief);
  const slope = towardLight.mul(rise.negate()).mul(resolved(footprint, 0.5 / DIRT.clods));

  const region = mx_fractal_noise_float(vec3(point.mul(1.6), 8), 3, 2, 0.5)
    .mul(0.5)
    .add(0.5);
  const hue = mix(vec3(1.08, 0.98, 0.86), vec3(0.9, 0.93, 0.98), clamp(region, 0, 1));
  const patches = mx_noise_float(vec3(point.mul(6), 2))
    .mul(0.12)
    .add(1);
  const grain = mx_cell_noise_float(point.mul(500)).sub(0.5).mul(0.18).mul(resolved(footprint, 0.002));
  const soil = hue.mul(patches).mul(clods.mul(0.12).add(1)).add(grain);
  const pebble = vec3(1.3, 1.27, 1.22).mul(mx_cell_noise_float(point.mul(DIRT.stones)).mul(0.4).add(0.8));
  const grass = smoothstep(
    0.62,
    0.78,
    mx_fractal_noise_float(vec3(point.mul(2.2), 21), 3, 2, 0.5)
      .mul(0.5)
      .add(0.5),
  )
    .mul(
      mx_noise_float(vec3(point.mul(vec2(220, 60)), 5))
        .mul(0.4)
        .add(0.6),
    )
    .mul(0.7);
  return {
    albedo: mix(mix(soil, pebble, stones), vec3(0.92, 1.04, 0.6), grass),
    gloss: stones.mul(2).add(1),
    slope,
  };
}

const FLOOR_SURFACES = freeze({ tile: tileSurface, wood: woodSurface, dirt: dirtSurface });

/**
 * Composite an infinite floor into a background colour without scene geometry.
 *
 * The view ray is intersected with the floor plane at the model's base. The
 * floor is shaded with its procedural surface, a light pool, relief lit from
 * the preset light and Fresnel-weighted mirrored reflections on glossy
 * patches, and dissolves into the backdrop with distance from the model and
 * toward the horizon, so it never draws over or clips an animated model.
 */
function compositeFloor({ direction, backdrop, reflection, look, palette, floor, lightDirection }) {
  const above = cameraPosition.y.sub(floor.height);
  const down = direction.y.negate();
  const visible = step(1e-5, above).mul(step(1e-5, down));
  const scale = floor.scale;
  const travel = min(above.div(max(down, 1e-5)), scale.mul(400));
  const local = cameraPosition.xz.add(direction.xz.mul(travel)).sub(floor.center);
  const point = local.div(scale).toVar();
  const radius = length(point);
  const span = fwidth(point);
  const footprint = max(max(span.x, span.y), 1e-6).toVar();

  let wet = float(0);
  let gloss = float(look.gloss);
  if (look.puddles) {
    // Large-scale noise picks out mirror-like patches; their edges widen with
    // screen-space derivatives so distant patches stay filtered.
    const patches = mx_fractal_noise_float(vec3(point.div(look.patch), 0), 4, 2, 0.5)
      .mul(0.5)
      .add(0.5)
      .toVar();
    const edge = max(fwidth(patches), 0.02);
    wet = smoothstep(edge.negate().add(0.6), edge.add(0.66), patches).toVar();
  }
  const material = FLOOR_SURFACES[look.surface]({ point, footprint, lightDirection });
  // Standing water fills relief and seams, leaving a flat mirror.
  const slope = material.slope.mul(wet.oneMinus()).toVar();
  gloss = clamp(gloss.mul(material.gloss).add(wet.mul(look.puddles ?? 0)), 0, 1);

  // Lambert response of the tilted surface relative to the flat floor.
  const [lx, ly, lz] = lightDirection;
  const lift = Math.max(ly, 0.3);
  const relief = clamp(
    slope.x
      .mul(lx / lift)
      .add(slope.y.mul(lz / lift))
      .oneMinus(),
    0.7,
    1.3,
  );
  const soaked = wet.mul(look.soak ?? 0).oneMinus();
  const pool = exp(radius.div(look.poolRadius).pow(2).negate());
  const diffuse = color(palette.floor)
    .mul(material.albedo)
    .mul(pool.mul(look.pool).add(look.base))
    .mul(relief)
    .mul(soaked);

  const fresnel = down.oneMinus().pow(5).mul(0.96).add(0.04);
  // Mirror about the tilted normal, to first order in the slope.
  const bend = down.mul(2);
  const mirrored = normalize(vec3(direction.x.sub(bend.mul(slope.x)), down, direction.z.sub(bend.mul(slope.y))));
  const surface = mix(diffuse, reflection(mirrored, gloss), fresnel.mul(gloss));

  const haze = max(exp(radius.div(look.haze).negate()).oneMinus(), smoothstep(-0.03, 0, direction.y));
  return mix(backdrop, mix(surface, backdrop, haze), visible);
}

/** Build a preset's displayed background and, for atmospheres, its floorless sky for IBL. */
function createPresetBackground(preset, floor) {
  const { palette, lightDirection, floor: look } = preset;
  const sky = preset.atmosphere ? createAtmosphereNode(preset.atmosphere) : null;
  const room = sky ? null : createRoomBackdrop(preset);
  const light = vec3(...lightDirection);
  // The scattering sky depends on vertex-stage varyings and cannot be sampled
  // along another ray, so atmospheres reflect the palette's sky gradient.
  const reflection = (mirrored, gloss) => {
    const reflected = room ? room(mirrored) : skyGradient(mirrored, palette, lightDirection);
    if (!look.glint) return reflected;
    const glint = min(max(0, mirrored.dot(light)).pow(GLINT_POWER).mul(look.glint).mul(gloss), MAX_GLINT);
    return reflected.add(color(palette.sun).mul(glint));
  };
  const display = Fn(() => {
    const direction = normalWorldGeometry.normalize();
    const backdrop = sky ? sky.rgb : room(direction);
    return vec4(compositeFloor({ direction, backdrop, reflection, look, palette, floor, lightDirection }), 1);
  })();
  return { display, sky };
}

function createAtmosphereNode(atmosphere) {
  const direction = normalWorldGeometry.normalize();
  const sky = new SkyMesh();
  for (const [name, value] of Object.entries(atmosphere.sky)) sky[name].value = value;
  sky.cloudSpeed.value = 0;
  sky.cloudCoverage.value = 0;
  sky.sunPosition.value.fromArray(atmosphere.sunDirection);
  sky.showSunDisc.value = true;
  // SkyMesh initializes scattering varyings in its vertex node. Reuse that
  // initialization and its r185 world-position varying for the background
  // sphere, while Three.js keeps its own far-plane projection. The zero-valued
  // dependency includes those vertex assignments without changing sky color.
  const initializeSky = Fn(() => {
    const projection = sky.material.vertexNode.toVar();
    varyingProperty('vec3', 'v_positionWorld').assign(cameraPosition.add(normalWorldGeometry));
    return projection;
  })().toVertexStage();
  const scattering = sky.material.colorNode.add(initializeSky.mul(0));
  const cloudField = Fn(([p]) => mx_fractal_noise_float(vec3(p, 0), 5, 2.1, 0.5).mul(0.5).add(0.5));
  const node = Fn(() => {
    // Compress sky highlights in linear space for both background and IBL.
    // Models retain the viewport's existing sRGB/NoToneMapping contract.
    const skyColor = neutralToneMapping(scattering.rgb, atmosphere.exposure);
    const { coverage, density, elevation, scale } = atmosphere.clouds;
    const uv = direction.xz.div(max(direction.y, 0.05)).mul((scale * 1000) / (1 - elevation * 0.9));
    const field = cloudField(uv).toVar();
    const threshold = 1 - coverage;
    // Filter at the current render resolution; fade distant clouds into
    // the atmospheric horizon instead of adding fog to inspection geometry.
    const edgeWidth = max(fwidth(field).mul(1.5), 0.015);
    const mask = smoothstep(edgeWidth.negate().add(threshold), edgeWidth.add(threshold + 0.12), field)
      .mul(density)
      .mul(smoothstep(0.02, 0.22, direction.y));
    const sun = sky.sunPosition;
    const towardSun = vec2(sun.x, sun.z).div(max(sun.y, 0.15)).mul(0.12);
    const sunDensity = max(0, cloudField(uv.add(towardSun)).sub(threshold));
    const transmission = exp(sunDensity.mul(-5));
    const rim = max(0, field.sub(threshold).sub(sunDensity)).mul(2);
    const forward = max(0, direction.dot(sun)).pow(6);
    const illumination = transmission.mul(max(sun.y, 0).mul(0.5).add(0.3)).add(rim).add(forward.mul(0.25));
    const cloudColor = color(0x7889aa).mul(0.25).add(color(atmosphere.sunColor).mul(illumination));
    const cloudedSky = mix(skyColor, cloudColor, mask);
    return vec4(mix(color(atmosphere.groundColor), cloudedSky, smoothstep(-0.35, 0.02, direction.y)), 1);
  })();
  // SkyMesh supplies its existing scattering nodes; its unused mesh resources
  // never enter the viewer scene. The renderer owns the background geometry.
  sky.geometry.dispose();
  sky.material.dispose();
  return node;
}

function createLightCard({ color, intensity, position, size, target = [0, 0, 0] }) {
  const card = new THREE.Mesh(
    new THREE.PlaneGeometry(1, 1),
    new THREE.MeshLambertMaterial({
      color: 0x000000,
      emissive: color,
      emissiveIntensity: intensity,
      side: THREE.DoubleSide,
    }),
  );
  card.position.fromArray(position);
  card.scale.set(size[0], size[1], 1);
  card.lookAt(new THREE.Vector3().fromArray(target));
  return card;
}

function createRoomCaptureScene(id, { capture, palette, lightDirection }) {
  const captureScene = new THREE.Scene();
  captureScene.userData.iblPresetId = id;
  const [width, , depth] = capture.room.size;
  const room = new THREE.Mesh(
    new THREE.BoxGeometry(...capture.room.size),
    new THREE.MeshStandardMaterial({ color: capture.room.color, side: THREE.BackSide }),
  );
  room.position.y = capture.room.y;
  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(width, depth),
    new THREE.MeshStandardMaterial({ color: palette.floor }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = capture.floorY;
  const direction = new THREE.Vector3().fromArray(lightDirection);
  const mainCard = createLightCard({
    color: palette.sun,
    ...capture.mainCard,
    position: direction.clone().multiplyScalar(capture.mainCard.distance).toArray(),
  });
  const roomLight = new THREE.PointLight(capture.roomLight.color, CAPTURE_ROOM_LIGHT_INTENSITY, 0, 2);
  roomLight.position.copy(direction).multiplyScalar(capture.roomLight.distance);
  captureScene.add(room, floor, mainCard, ...capture.cards.map(createLightCard), roomLight);
  return captureScene;
}

function disposeCaptureScene(captureScene) {
  if (!captureScene) return;
  const resources = new Set();
  captureScene.traverse((object) => {
    if (object.geometry) resources.add(object.geometry);
    const materials = Array.isArray(object.material) ? object.material : object.material ? [object.material] : [];
    for (const material of materials) resources.add(material);
  });
  for (const resource of resources) resource.dispose?.();
  captureScene.clear();
}

function captureLightState(light) {
  return {
    color: light.color.clone(),
    groundColor: light.groundColor?.clone(),
    intensity: light.intensity,
  };
}

function restoreLightState(light, state) {
  light.color.copy(state.color);
  if (state.groundColor) light.groundColor.copy(state.groundColor);
  light.intensity = state.intensity;
}

/**
 * Create the synchronously constructible environment controller.
 *
 * Environment presets define baseline scene lighting. The movable key light
 * in scene.js remains an independent user-controlled inspection light layered
 * on top of these presets.
 */
export function createEnvironmentController({
  renderer,
  scene,
  ambientLight,
  hemisphereLight,
  lightTarget,
  onVisualChange = null,
}) {
  if (!renderer || !scene || !ambientLight || !hemisphereLight || !lightTarget) {
    throw new Error('EnvironmentController needs a renderer, scene, viewer lights and target.');
  }

  const originalBackground = scene.background;
  const originalBackgroundNode = scene.backgroundNode;
  const originalEnvironment = scene.environment;
  const originalEnvironmentIntensity = scene.environmentIntensity;
  const originalAmbient = captureLightState(ambientLight);
  const originalHemisphere = captureLightState(hemisphereLight);
  const accentLight = new THREE.DirectionalLight(0xffffff, 0);
  accentLight.visible = false;
  accentLight.target = lightTarget;
  // Preset positions are offsets from the model target. The existing
  // movable key light updates this target whenever the model is reframed.
  lightTarget.add(accentLight);

  let currentPresetId = 'default';
  let disposed = false;
  let preparationEnabled = false;
  let pmremGenerator = null;
  // Shared by every preset's background floor; updated from the locked ground
  // frame without rebuilding nodes or regenerating IBL.
  const floorUniforms = freeze({
    height: uniform(0),
    center: uniform(new THREE.Vector2()),
    scale: uniform(DEFAULT_FLOOR_SCALE),
  });
  const iblResources = new Map(
    Object.keys(PRESET_DEFINITIONS).map((id) => [
      id,
      {
        promise: null,
        target: null,
        backgroundNode: null,
        skyNode: null,
        texture: null,
        attempted: false,
        available: false,
        error: null,
        generationCount: 0,
      },
    ]),
  );

  function applyAccent(config) {
    accentLight.visible = !!config;
    accentLight.intensity = config?.intensity ?? 0;
    if (!config) return;
    accentLight.color.set(config.color);
    // This is local to lightTarget, so the accent follows offset models.
    accentLight.position.fromArray(config.position);
  }

  function applyLights(preset, intensities) {
    ambientLight.color.set(preset.lightColors.ambient);
    ambientLight.intensity = intensities.ambient;
    hemisphereLight.color.set(preset.lightColors.sky);
    hemisphereLight.groundColor.set(preset.lightColors.ground);
    hemisphereLight.intensity = intensities.hemisphere;
    applyAccent(preset.accent);
  }

  function restoreEnvironment() {
    scene.environment = originalEnvironment;
    scene.environmentIntensity = originalEnvironmentIntensity;
  }

  function applyDefault() {
    scene.background = originalBackground;
    scene.backgroundNode = originalBackgroundNode;
    restoreEnvironment();
    restoreLightState(ambientLight, originalAmbient);
    restoreLightState(hemisphereLight, originalHemisphere);
    applyAccent(null);
  }

  function applyPreset(id) {
    if (id === 'default') {
      applyDefault();
      return;
    }
    const preset = ENVIRONMENT_PRESETS[id];
    const resource = iblResources.get(id);
    if (!resource.backgroundNode) {
      const { display, sky } = createPresetBackground(preset, floorUniforms);
      resource.backgroundNode = display;
      resource.skyNode = sky;
    }
    scene.background = originalBackground;
    scene.backgroundNode = resource.backgroundNode;
    applyLights(preset, resource.available ? preset.ibl : preset.lights);
    if (resource.available) {
      scene.environment = resource.texture;
      scene.environmentIntensity = preset.ibl.intensity;
    } else restoreEnvironment();
  }

  function setPreset(id) {
    if (disposed) return false;
    const preset = ENVIRONMENT_PRESETS[id];
    if (!preset) return false;
    currentPresetId = preset.id;
    applyPreset(id);
    if (preparationEnabled) void prepareResource(id, true);
    return true;
  }

  function generateIblResource(id) {
    const preset = ENVIRONMENT_PRESETS[id];
    const resource = iblResources.get(id);
    resource.attempted = true;
    resource.error = null;
    let captureScene = null;
    let generatedTarget = null;
    let rendererState = null;
    try {
      rendererState = THREE.RendererUtils.saveRendererState(renderer);
      renderer.setMRT(null);
      pmremGenerator ||= new THREE.PMREMGenerator(renderer);
      if (preset.atmosphere) {
        captureScene = new THREE.Scene();
        captureScene.userData.iblPresetId = id;
        // Capture the sky alone: the floor follows the model, while the
        // cached reflection map must not.
        captureScene.backgroundNode = resource.skyNode;
      } else captureScene = createRoomCaptureScene(id, preset);
      generatedTarget = pmremGenerator.fromScene(captureScene, PMREM.sigma, PMREM.near, PMREM.far, {
        size: PMREM.size,
      });
      if (!generatedTarget?.texture) {
        throw new Error(`${id} PMREM generation returned no texture.`);
      }
      resource.target = generatedTarget;
      resource.texture = generatedTarget.texture;
      resource.available = true;
      resource.generationCount += 1;
      generatedTarget = null;
      return true;
    } finally {
      if (rendererState) THREE.RendererUtils.restoreRendererState(renderer, rendererState);
      generatedTarget?.dispose?.();
      disposeCaptureScene(captureScene);
    }
  }

  function prepareResource(id, retry = false) {
    const resource = iblResources.get(id);
    if (disposed || !resource) return Promise.resolve(false);
    if (resource.promise) return resource.promise;
    // Only another explicit selection retries a failure; passive preparation
    // and repeated selections during one pending capture never schedule more work.
    if (resource.available || (resource.attempted && !retry)) return Promise.resolve(resource.available);
    resource.promise = (async () => {
      try {
        // Let startup and rapid preset changes settle before expensive GPU work.
        await new Promise((resolve) => setTimeout(resolve, 0));
        if (disposed || currentPresetId !== id) return false;
        const generated = generateIblResource(id);
        if (generated && !disposed && currentPresetId === id) {
          applyPreset(id);
          onVisualChange?.();
        }
        return generated;
      } catch (error) {
        resource.error = error?.message || String(error);
        console.debug(`${id} environment preparation failed; using baseline lighting.`, error);
        return false;
      } finally {
        resource.promise = null;
      }
    })();
    return resource.promise;
  }

  function prepare() {
    if (disposed) return Promise.resolve(false);
    preparationEnabled = true;
    return prepareResource(currentPresetId);
  }

  function getDebugState() {
    const resources = [...iblResources.values()];
    return {
      preset: currentPresetId,
      environmentActive: resources.some((resource) => resource.texture && scene.environment === resource.texture),
      environmentIntensity: scene.environmentIntensity,
      preparationInFlight: resources.some((resource) => resource.promise !== null),
      resources: Object.fromEntries(
        [...iblResources.entries()].map(([id, { attempted, available, generationCount, error }]) => [
          id,
          { attempted, available, generationCount, error },
        ]),
      ),
      totalPmremGenerationCount: resources.reduce((total, resource) => total + resource.generationCount, 0),
      atmosphere: getPreset().atmosphere || null,
      floorActive:
        currentPresetId !== 'default' && scene.backgroundNode === iblResources.get(currentPresetId)?.backgroundNode,
      ground: {
        height: floorUniforms.height.value,
        center: floorUniforms.center.value.toArray(),
        scale: floorUniforms.scale.value,
      },
    };
  }

  /**
   * Place the background floor under the model. `frame` is the shadow
   * controller's locked `{ y, center: [x, z], size }`, or null for the origin.
   */
  function setGround(frame) {
    floorUniforms.height.value = frame?.y ?? 0;
    floorUniforms.center.value.fromArray(frame?.center ?? [0, 0]);
    floorUniforms.scale.value = frame?.size ?? DEFAULT_FLOOR_SCALE;
  }

  function getDominantLightDirection() {
    const direction = getPreset().lightDirection;
    return direction ? [...direction] : null;
  }

  function getPreset() {
    return ENVIRONMENT_PRESETS[currentPresetId];
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    currentPresetId = 'default';
    applyDefault();
    for (const resource of iblResources.values()) {
      resource.target?.dispose?.();
      resource.backgroundNode?.dispose();
      resource.skyNode?.dispose();
      resource.target = null;
      resource.backgroundNode = null;
      resource.skyNode = null;
      resource.texture = null;
      resource.available = false;
    }
    pmremGenerator?.dispose();
    pmremGenerator = null;
    lightTarget.remove(accentLight);
  }

  return {
    prepare,
    setPreset,
    getPreset,
    getDominantLightDirection,
    getDebugState,
    setGround,
    dispose,
  };
}
