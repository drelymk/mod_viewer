# Material interpretation and rendering

- Use vendored Three.js WebGPURenderer/TSL Node Materials; verify the initialized
  backend and never silently fall back to WebGL2. Game, runtime, texture API and
  material kind are distinct. Resolve per-mesh profiles from structural evidence
  and `(game, texture_api, material_kind)` with validated base fallback; weak
  component names or filenames never activate specialized semantics.
- Character AO uses spatial filtering with no temporal settling. Keep
  character-only opaque depth/normals, ambient-only composition, independent
  emission bloom, and the direct path when disabled or suppressed by wireframe.
  Packed normal targets stay byte textures across pass setup and graph switches.
  Directional shadow filtering stays deterministic at close zoom.
- Initialize the oriented geometry normal before branching on a normal map (shared
  expressions in an inactive branch can leave the fallback at zero). Cache lit
  output before rim/debug paths reuse RGB and alpha so lighting constants are
  generated once.
- Deduplicate immutable `material_profile_id` metadata; conflicting IDs are
  programming errors. Keep packed RGBA and role bindings intact; never map channels
  to stock PBR inputs without evidence. Binding updates use stable texture/enabled
  nodes and valid placeholders, never material rebuilds or shader patches.
- Genshin LightMap: R response, G toon shadow, B specular area, A regions; the B
  highlight threshold follows the Toon toggle, ordinary shading keeps the physical
  glossy response. ZZZ: LightMap G gives conservative metalness, MaterialMap B
  specular response; MaterialMap R is ID data and G stays packed/unknown.
- Genshin and ZZZ reconstruct `normal_map` RG in TSL, WuWa `normal_data` RG.
  Zero-filled RG falls back to geometry normals; B/A stay diagnostic or
  profile-specific. RabbitFX uses LightMap G for shadow; its base is shadow-only
  and Normalmap B/A response needs reliable exact body-profile evidence.
- Gate diagnostic modes by material capability; unsupported modes keep normal
  rendering. Diagnostic-only channels never trigger startup requests.
