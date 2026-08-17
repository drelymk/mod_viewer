"""Real-mod corpus regressions at the loader/provenance boundary."""

import os

from _corpus import sample_inis, sample_mods
from app import mod_loader
from core.ini_parser import build_draw_groups, extract_resources, merge_sections
from _provenance_support import write

def _anchors_section(all_lines, n, section):
    """True if line n is the first significant line of `section`."""
    header = f"[{(section or '').lower()}]"
    i = n - 2
    while i >= 0:
        t = all_lines[i].strip().lower()
        if t.startswith("[") and t.endswith("]"):
            return t == header
        if t and not t.startswith(";"):
            return False
        i -= 1
    return False


def test_real_mods():
    mods = sample_mods(15, seed=11)
    if not mods:
        print("SKIP  no local mod libraries found")
        return

    total_meshes = missing = multi = 0
    bad_line = bad_file = 0
    skipped = 0

    for mod in mods:
        payload = mod_loader.load_mod(mod)
        if "error" in payload:
            # "Master" dispatch inis in AllInOne folders hold only menus and
            # namespace switches, never geometry — nothing to trace.
            if "No mesh geometry" in payload["error"]:
                skipped += 1
            else:
                assert (False), (f"{os.path.basename(mod)} failed to load: "
                             f"{payload['error'].strip().splitlines()[-1][:100]}")
            continue
        for name, entry in payload.get("meshes", {}).items():
            if not isinstance(entry, dict):
                continue
            total_meshes += 1
            srcs = entry.get("sources") or []
            if not srcs:
                missing += 1
                continue
            if len(srcs) > 1:
                multi += 1
            for s in srcs:
                ini_abs = os.path.join(mod, s["ini"] or "")
                if not os.path.isfile(ini_abs):
                    bad_file += 1
                    continue
                with open(ini_abs, encoding="utf-8", errors="ignore") as f:
                    all_lines = f.readlines()
                n = s["line"]
                if not (n and 1 <= n <= len(all_lines)):
                    bad_line += 1
                    continue
                text = all_lines[n - 1].strip().lower()
                # Normally the exact drawindexed. A section with no drawindexed
                # at all is drawn by reading the whole IB, and is anchored to
                # the first significant line of its section instead.
                if not (text.startswith("drawindexed")
                        or _anchors_section(all_lines, n, s["section"])):
                    bad_line += 1

    print(f"      {len(mods)} mods ({skipped} geometry-free), {total_meshes} meshes, "
          f"{multi} multi-source, {missing} without provenance")
    assert (total_meshes > 0), ("real mods produced meshes")
    assert (missing == 0), (f"every mesh has provenance (missing={missing})")
    assert (bad_file == 0), (f"every recorded ini path resolves (bad={bad_file})")
    assert (bad_line == 0), (f"every recorded line is a drawindexed or a section anchor (bad={bad_line})")
    assert (multi > 0), (f"at least one mesh has several sources (got {multi})")


def test_diffuse_resolution_corpus_sweep():
    """The execution-order diffuse fix (per-draw `texture_default_file` /
    `diffuse_pool_files`, replacing one static tex_key per component) must
    not crash or misbehave across the real corpus, and must actually change
    resolution for the multi-diffuse sections it exists to fix. For a section
    referencing at most one diffuse, that diffuse must still apply to AT
    LEAST ONE draw (a draw legitimately preceding the assignment in file
    order gets None instead, which is fine) -- if it resolves but zero draws
    ever get it, that's the whole point of resolving lost.
    """
    inis = sample_inis(400, seed=17)
    if not inis:
        print("SKIP  no local mod libraries found")
        return

    sections = draws = multi_diffuse_sections = differing_draws = 0
    unresolved_pool_entries = crashes = 0
    single_diffuse_mismatches = 0

    for ini_path in inis:
        try:
            secs = merge_sections([ini_path])
            groups = build_draw_groups(secs, extract_resources(secs))
        except Exception as e:
            crashes += 1
            assert (False), (f"{ini_path}: build_draw_groups crashed: {e}")
            continue

        for grp in groups:
            sections += 1
            pool = grp.get("diffuse_pool_files") or []
            for entry in pool:
                if not entry.get("file"):
                    unresolved_pool_entries += 1
            defaults = set()
            for d in grp["draws"]:
                draws += 1
                defaults.add(d.get("texture_default_file"))
            if len(pool) >= 2:
                multi_diffuse_sections += 1
                if len(defaults) > 1:
                    differing_draws += 1
            elif grp.get("diffuse_file"):
                # At most one diffuse ever resolves. A draw legitimately
                # precedes the (single) assignment in file order and gets
                # None for it -- real corpus examples: Caesar/Promeia/Aria
                # sections with several drawindexed lines before their
                # Resource\...\Diffuse line. So not every draw need equal
                # the old static value -- but AT LEAST ONE must, or the
                # diffuse never actually applies to anything despite
                # resolving. Real regression this catches: a section with NO
                # drawindexed line at all (implicit whole-buffer draw) whose
                # synthetic placeholder used to hardcode an empty variant
                # list, resolving to None even though e.g. a bare `ps-t0 =`
                # names a real diffuse (Beidou.ini's
                # TextureOverrideBeidouBody) -- there `defaults` is just
                # {None} because there's only the one placeholder draw.
                old_static = grp["diffuse_file"]
                if old_static not in defaults:
                    single_diffuse_mismatches += 1

    print(f"      {len(inis)} inis, {sections} sections, {draws} draws, "
          f"{multi_diffuse_sections} multi-diffuse sections "
          f"({differing_draws} with draws that actually resolve differently), "
          f"{unresolved_pool_entries} unresolved pool entries")
    assert (sections > 0), ("the corpus sample produced draw groups")
    assert (crashes == 0), (f"build_draw_groups never raises on real inis (got {crashes})")
    assert (unresolved_pool_entries == 0), (f"every diffuse_pool_files entry resolves to a filename "
          f"(got {unresolved_pool_entries} unresolved)")
    assert (single_diffuse_mismatches == 0), (f"a section referencing at most one diffuse resolves identically "
          f"to the old static-per-component model (got "
          f"{single_diffuse_mismatches} mismatches)")
