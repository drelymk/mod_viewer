"""Verified WWMI shader/slot semantic mappings.

WWMI's extracted texture metadata identifies a texture hash, shader pair and
pixel-shader slot, but it does not name the semantic role. In particular, slot
numbers are not global semantic names; every supported mapping is keyed by the
complete shader/slot tuple.
"""


# Keys are (vertex-shader hash, pixel-shader hash, ps-tN slot). These tuples
# are the first verified WWMI profile: role-named replacement overrides and
# the corresponding extracted texture/shader evidence agree on diffuse and
# normal roles. Other WWMI maps remain diagnostic-only until verified.
_PROFILES = {
    ("0ccd030bff8b515c", "21176cf68a65ab7a", 0): "diffuse",
    ("0ccd030bff8b515c", "21176cf68a65ab7a", 3): "diffuse",
    ("5050624d37f1dd08", "32414b557630d98d", 0): "diffuse",
    ("5050624d37f1dd08", "32414b557630d98d", 3): "diffuse",
    ("5102d7edd774359e", "94d9d5e981938d52", 3): "diffuse",
    ("5d60ebdc89fe3833", "21176cf68a65ab7a", 0): "diffuse",
    ("60b893ec7f585976", "32414b557630d98d", 3): "diffuse",
    ("676fdbd61b302294", "92ca4bd985fe6887", 3): "diffuse",
    ("ad93dfe819da9350", "ca134b7ad59cdf8c", 0): "diffuse",
    ("ad93dfe819da9350", "ca134b7ad59cdf8c", 3): "diffuse",
    ("aef4fc536fbff1e7", "320a753b019eff67", 3): "diffuse",
    ("ba18f5925530d329", "0a2f71325be0686c", 1): "diffuse",
    ("ba18f5925530d329", "d9a2bafab96aaab6", 1): "diffuse",
    ("babb30f8c1510db4", "3df800c350681ec9", 1): "diffuse",
    ("bbabe18b97a63509", "3df800c350681ec9", 0): "diffuse",
    ("bbabe18b97a63509", "3df800c350681ec9", 1): "diffuse",
    ("dc8efba6073d61bf", "1f0d1da54f8f19c2", 0): "diffuse",
    ("de762416efa3d221", "1bc2b394bbf15ef6", 3): "diffuse",
    ("de762416efa3d221", "99f47cdbb0c92896", 3): "diffuse",
    ("f0107aa8c39ca636", "32414b557630d98d", 0): "diffuse",
    ("f0107aa8c39ca636", "32414b557630d98d", 3): "diffuse",
    ("fd12d3374ac7a7dd", "259b766b59f72419", 0): "diffuse",
    ("fd12d3374ac7a7dd", "259b766b59f72419", 3): "diffuse",
    ("60b893ec7f585976", "32414b557630d98d", 0): "normal_map",
    ("de762416efa3d221", "1bc2b394bbf15ef6", 0): "normal_map",
    ("de762416efa3d221", "99f47cdbb0c92896", 0): "normal_map",
    ("f59379b10554d2ab", "6d947d37ebbd2bae", 0): "normal_map",
}


def resolve_texture_role(*, vs_hash, ps_hash, slot):
    """Return a verified role for one WWMI shader/slot tuple, if known."""
    if (not isinstance(vs_hash, str) or not isinstance(ps_hash, str)
            or isinstance(slot, bool) or not isinstance(slot, int)
            or slot < 0):
        return None
    return _PROFILES.get((vs_hash.casefold(), ps_hash.casefold(), slot))
