# Parsing, execution order and identity

- `canonical_var_names()` governs case-insensitive spelling in toggles, defaults,
  menu state and DNF; partial normalization can make gates fail open.
- Preserve `SrcLine` provenance through `line_source()` and every merged draw's
  full `sources` list; edits fan out to all authored sources. Draw labels are
  unique across active INIs and distinct from UI display names.
- Recognize `elif` and `else if`. Draw DNF keeps contradictions and treats empty
  DNF as true; only state-rule DNF may drop contradictory groups.
- Draw discovery selects generic `pre`/`post` phases before handling bindings or
  draws. Plain CommandList calls follow the caller's phase; explicit phase calls
  and CustomShader calls run both phases. Keep authored draw/call ordinals.
  Resolve complete trimmed `run` targets (internal spaces and Unicode included);
  never fall back to a shorter first-token section.
- Follow explicit `Resource = copy ...` and compute UAV copy/ref chains for
  geometry and animation sources, keeping ordered alternatives and stopping
  cycles; never infer a source through an unsupported shader operation.
- A direct root INI anchors bounded depth/count discovery; never merge unrelated
  library/category folders. Resolve resources relative to the declaring INI but
  publish identities relative to the selected mod root.

## Menus and control data flow

- Menu recognition needs nested self-assignment structure and outer guards;
  never guess structure. Increment/modulo cycles expose all `0..N-1` positions.
  Unsupported `&&` forms may weaken a guard rather than hide geometry.
- Slot and mouse menus share action resolution and artwork matching. Local helper
  calls are bounded and need proven finite cycles; latched buttons need matching
  press/release hit regions; keep paired button actions. Hit testing and drawing
  may be separate passes.
- Control data flow is per INI and bounded: join unknown branches, keep caller
  inputs through local helpers, reject unknown writes, recursion and conflicting
  wrap ranges. Match artwork by selector identity and image binding, never file
  order. A failed call tree discards its own artwork evidence and budget only.
- Lazy diagnostics explain unresolved menu cycles and clamped compute controls
  without inventing shader deformations.

## Texture bindings and slots

- Texture bindings are per-draw execution-order state: keep the latest applicable
  assignment, conditional alternatives and no-map fallbacks for every role. Never
  collapse to a component-level texture.
- Texture checks stay separate from resource hashes and slot writes. Supported
  pixel-slot checks and the external SkinTexture adapter gate hash replacement in
  that INI's explicit draw scopes; unknown external execution and implicit-only
  INIs keep compatibility; unused helpers never create a checking scope. An unknown
  native slot may match Asset hashes; an explicitly assigned resource without a
  verified hash cannot activate unrelated replacements.
- Ordered, conditional slot writes are kept separately from material roles. Only
  semantic or unambiguous component hash evidence promotes them to role rules; an
  ambiguous numeric slot stays a manual choice. Exported slot declarations need
  matching component geometry, an independently labelled Asset hash/role and the
  bound mod-relative filename; once established for a draw the role applies to
  its conditional writes, including null. Keep declarations per INI and never
  infer roles from slot numbers.
