"""BC7 recoloring, mip intent propagation, and bounded fitting workers."""

from array import array
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass
import logging
import multiprocessing
import os

from core.textures import bc7 as _bc7_codec
from core.textures.color_adjustment import (
    PreparedColorAdjustment, apply_prepared_color_adjustment,
    apply_prepared_color_u8, prepare_color_adjustment,
)

from .errors import TextureSaveError


_LOGGER = logging.getLogger(__name__)
_BC7_PARALLEL_THRESHOLD = 4096
_BC7_CHUNK_SIZE = 256
_BC7_MAX_WORKERS = 6
_BC7_MAX_IN_FLIGHT_FACTOR = 2


@dataclass(frozen=True)
class _BC7BlockIntent:
    classes: tuple


@dataclass(frozen=True)
class _BC7BlockJob:
    start: int
    source_block: bytes
    source_pixels: tuple
    target_pixels: tuple
    valid_width: int
    valid_height: int


@dataclass(frozen=True)
class _BC7SingleIntentJob:
    start: int
    source_block: bytes
    adjustment: PreparedColorAdjustment
    valid_width: int
    valid_height: int


@dataclass(frozen=True)
class _BC7WeightedSingleIntentJob:
    start: int
    source_block: bytes
    adjustment: PreparedColorAdjustment
    changed_counts: tuple
    total_counts: tuple
    valid_width: int
    valid_height: int


@dataclass(frozen=True)
class _BC7BlockResult:
    start: int
    block: bytes
    source_error: int
    candidate_error: int
    error: str = None
    error_code: str = None


def _unit_bounds(mip, index):
    unit_x = (index % mip.units_x) * 4
    unit_y = (index // mip.units_x) * 4
    return (unit_x, unit_y, min(4, mip.width - unit_x),
            min(4, mip.height - unit_y))



def _intent_region_bounds(index, source_size, target_size):
    """Return the source-pixel range represented by one lower-mip pixel."""
    start = (index * source_size) // target_size
    end = ((index + 1) * source_size) // target_size
    return start, max(start + 1, end)


def _downsample_intent_counts(claims, counts, source_width, source_height,
                              target_width, target_height, class_count,
                              source_max_count=1):
    """Aggregate mip-0 adjustment weights into one lower-mip level."""
    source_pixels = source_width * source_height
    target_pixels = target_width * target_height
    if counts is None:
        if len(claims) != source_pixels:
            raise TextureSaveError(
                "texture_validation_failed",
                "Mip-0 color intent does not match the source texture size.")
    elif (len(counts) != class_count
          or any(len(item) != source_pixels for item in counts)):
        raise TextureSaveError(
            "texture_validation_failed",
            "Lower-mip color intent does not match the source texture size.")
    if class_count <= 0 or target_width <= 0 or target_height <= 0:
        raise TextureSaveError(
            "texture_validation_failed", "DDS mip dimensions are invalid.")
    max_region_width = (source_width + target_width - 1) // target_width
    max_region_height = (source_height + target_height - 1) // target_height
    max_count = source_max_count * max_region_width * max_region_height
    typecode = "H" if max_count <= 0xFFFF else "I"
    result = tuple(
        array(typecode, [0]) * target_pixels for _ in range(class_count))
    for y in range(target_height):
        source_y0, source_y1 = _intent_region_bounds(
            y, source_height, target_height)
        for x in range(target_width):
            source_x0, source_x1 = _intent_region_bounds(
                x, source_width, target_width)
            target_index = y * target_width + x
            if counts is None:
                for source_y in range(source_y0, source_y1):
                    start = source_y * source_width + source_x0
                    end = source_y * source_width + source_x1
                    for intent_class in claims[start:end]:
                        if not isinstance(intent_class, int) or not (
                                0 <= intent_class < class_count):
                            raise TextureSaveError(
                                "texture_validation_failed",
                                "Mip-0 color intent contains an invalid class.")
                        result[intent_class][target_index] += 1
            else:
                for intent_class, source_counts in enumerate(counts):
                    total = 0
                    for source_y in range(source_y0, source_y1):
                        start = source_y * source_width + source_x0
                        end = source_y * source_width + source_x1
                        total += sum(source_counts[start:end])
                    result[intent_class][target_index] = total
    return result


def _downsample_single_intent_counts(
        changed_counts, total_counts, source_width, source_height,
        target_width, target_height):
    """Downsample changed and total mip-0 weights for one adjustment."""
    source_pixels = source_width * source_height
    if (len(changed_counts) != source_pixels
            or len(total_counts) != source_pixels):
        raise TextureSaveError(
            "texture_validation_failed",
            "Mip color intent does not match the source texture size.")
    if any(changed > total for changed, total in zip(
            changed_counts, total_counts)):
        raise TextureSaveError(
            "texture_validation_failed",
            "Changed color intent exceeds total mip weight.")
    max_region_width = (source_width + target_width - 1) // target_width
    max_region_height = (source_height + target_height - 1) // target_height
    max_count = (max(total_counts, default=0) * max_region_width
                 * max_region_height)
    typecode = "H" if max_count <= 0xFFFF else "I"
    changed_result = array(typecode, [0]) * (target_width * target_height)
    total_result = array(typecode, [0]) * (target_width * target_height)
    for y in range(target_height):
        source_y0, source_y1 = _intent_region_bounds(
            y, source_height, target_height)
        for x in range(target_width):
            source_x0, source_x1 = _intent_region_bounds(
                x, source_width, target_width)
            changed = 0
            total = 0
            for source_y in range(source_y0, source_y1):
                start = source_y * source_width + source_x0
                end = source_y * source_width + source_x1
                changed += sum(changed_counts[start:end])
                total += sum(total_counts[start:end])
            if changed > total:
                raise TextureSaveError(
                    "texture_validation_failed",
                    "Changed color intent exceeds total mip weight.")
            target_index = y * target_width + x
            changed_result[target_index] = changed
            total_result[target_index] = total
    return changed_result, total_result


def _bc7_intent_level(prepared):
    """Create the level-zero intent state used by direct BC7 Save."""
    base_mip = prepared.layout.mips[0]
    claims = getattr(prepared, "mip0_claims", None)
    adjustments = getattr(prepared, "intent_adjustments", None)
    if claims is None or not adjustments or len(claims) != (
            base_mip.width * base_mip.height):
        raise TextureSaveError(
            "texture_validation_failed",
            "Mip-0 color intent does not match the source texture size.")
    class_count = len(adjustments)
    changed_counts = bytearray(1 if value else 0 for value in claims)
    total_counts = bytearray(b"\x01") * len(claims)
    return {
        "level": 0,
        "width": base_mip.width,
        "height": base_mip.height,
        "class_count": class_count,
        "claims": claims,
        "changed_counts": changed_counts,
        "total_counts": total_counts,
        "single": class_count == 2,
        "counts": None,
        "max_count": 1,
        "affected_blocks": getattr(prepared, "mip0_affected_blocks", None),
    }


def _bc7_next_intent_level(state, target_width, target_height, class_count):
    """Advance direct-save intent by exactly one authored mip."""
    source_width, source_height = state["width"], state["height"]
    if state["single"]:
        changed_counts, total_counts = _downsample_single_intent_counts(
            state["changed_counts"], state["total_counts"], source_width,
            source_height, target_width, target_height)
        counts = None
        max_count = None
    else:
        total_counts = None
        counts = _downsample_intent_counts(
            state["claims"] if state["counts"] is None else None,
            state["counts"], source_width, source_height,
            target_width, target_height, class_count, state["max_count"])
        changed_counts = bytearray(
            1 if any(count[index] for count in counts[1:]) else 0
            for index in range(target_width * target_height))
        max_count = state["max_count"] * (
            (source_width + target_width - 1) // target_width) * (
            (source_height + target_height - 1) // target_height)
    return {
        "level": state["level"] + 1,
        "width": target_width,
        "height": target_height,
        "class_count": class_count,
        "claims": None,
        "changed_counts": changed_counts,
        "total_counts": total_counts if state["single"] else None,
        "single": state["single"],
        "counts": counts,
        "max_count": max_count,
        "affected_blocks": None,
    }


def _bc7_affected_blocks(changed, width, height, mip):
    """Return only BC7 blocks containing changed intent pixels."""
    if len(changed) != width * height:
        raise TextureSaveError(
            "texture_validation_failed",
            "Mip color intent does not match the source texture size.")
    affected = set()
    for pixel, selected in enumerate(changed):
        if not selected:
            continue
        x = pixel % width
        y = pixel // width
        affected.add((y // 4) * mip.units_x + x // 4)
    return sorted(affected)


def _bc7_weighted_single_rgb(
        source_rgb, adjustment, changed_count, total_count):
    """Resolve one RGB value from one lower-mip adjustment weight."""
    if changed_count > total_count:
        raise TextureSaveError(
            "texture_validation_failed",
            "Changed color intent exceeds total mip weight.")
    if not changed_count or not total_count:
        return source_rgb
    base = tuple(channel / 255.0 for channel in source_rgb)
    adjusted = apply_prepared_color_adjustment(base, adjustment)
    return tuple(min(255, max(0, round(value * 255.0)))
                  for value in (
                      (base[channel] * (total_count - changed_count)
                       + adjusted[channel] * changed_count) / total_count
                      for channel in range(3)))


def _bc7_intent_rgb(source_rgb, state, pixel, adjustments):
    """Resolve one target RGB value from the current mip's intent state."""
    base = tuple(channel / 255.0 for channel in source_rgb)
    if state["level"] == 0:
        intent_class = state["claims"][pixel]
        if not isinstance(intent_class, int) or not (
                0 <= intent_class < len(adjustments)):
            raise TextureSaveError(
                "texture_validation_failed",
                "Mip-0 color intent contains an invalid class.")
        if not intent_class:
            return source_rgb
        return apply_prepared_color_u8(
            source_rgb, adjustments[intent_class])

    if state["single"]:
        return _bc7_weighted_single_rgb(
            source_rgb, adjustments[1], state["changed_counts"][pixel],
            state["total_counts"][pixel])

    counts = [count[pixel] for count in state["counts"]]
    total_count = sum(counts)
    changed_count = sum(counts[1:])
    if not changed_count or not total_count:
        return source_rgb
    weighted = [base[channel] * counts[0] for channel in range(3)]
    for intent_class, count in enumerate(counts[1:], 1):
        if not count:
            continue
        adjusted = apply_prepared_color_adjustment(
            base, adjustments[intent_class])
        for channel in range(3):
            weighted[channel] += adjusted[channel] * count
    return tuple(min(255, max(0, round(
        value / total_count * 255.0))) for value in weighted)


def _bc7_block_intent_info(state, mip, block_index):
    """Classify one block's logical intent without changing the claims."""
    if state.get("level") != 0:
        return _bc7_weighted_block_intent_info(state, mip, block_index)
    claims = state.get("claims")
    width, height = state.get("width"), state.get("height")
    class_count = state.get("class_count")
    if (claims is None or width != mip.width or height != mip.height
            or len(claims) != width * height
            or (class_count is not None
                and (not isinstance(class_count, int) or class_count <= 0))):
        raise TextureSaveError(
            "texture_validation_failed",
            "Mip-0 color intent does not match the source texture size.")
    source_x, source_y, valid_width, valid_height = _unit_bounds(
        mip, block_index)
    classes = set()
    for row in range(valid_height):
        for column in range(valid_width):
            value = claims[(source_y + row) * width + source_x + column]
            if (not isinstance(value, int) or value < 0
                    or (class_count is not None and value >= class_count)):
                raise TextureSaveError(
                    "texture_validation_failed",
                    "Mip-0 color intent contains an invalid class.")
            if value:
                classes.add(value)
    ordered_classes = tuple(sorted(classes))
    return _BC7BlockIntent(ordered_classes)


def _bc7_validate_lower_single_state(state, mip):
    """Return validated one-adjustment lower-mip weight planes."""
    if (state.get("level", 0) <= 0
            or state.get("width") != mip.width
            or state.get("height") != mip.height):
        raise TextureSaveError(
            "texture_validation_failed",
            "Lower-mip BC7 weighted intent state is invalid.")
    changed = state.get("changed_counts")
    total = state.get("total_counts")
    expected = mip.width * mip.height
    try:
        changed_length = len(changed)
        total_length = len(total)
    except TypeError as error:
        raise TextureSaveError(
            "texture_validation_failed",
            "Lower-mip color intent does not match the source texture size.") \
            from error
    if changed_length != expected or total_length != expected:
        raise TextureSaveError(
            "texture_validation_failed",
            "Lower-mip color intent does not match the source texture size.")
    return changed, total


def _bc7_validate_lower_multi_counts(state, mip):
    """Return validated per-class lower-mip count planes."""
    if (state.get("level", 0) <= 0
            or state.get("width") != mip.width
            or state.get("height") != mip.height):
        raise TextureSaveError(
            "texture_validation_failed",
            "Lower-mip BC7 weighted intent state is invalid.")
    counts = state.get("counts")
    expected = mip.width * mip.height
    try:
        count_plane_count = len(counts)
    except (TypeError, AttributeError) as error:
        raise TextureSaveError(
            "texture_validation_failed",
            "Lower-mip color intent does not match the source texture size.") \
            from error
    class_count = state.get("class_count", count_plane_count)
    if (not isinstance(class_count, int) or class_count <= 0
            or count_plane_count != class_count):
        raise TextureSaveError(
            "texture_validation_failed",
            "Lower-mip color intent contains an invalid class count.")
    for count_values in counts:
        try:
            count_length = len(count_values)
        except TypeError as error:
            raise TextureSaveError(
                "texture_validation_failed",
                "Lower-mip color intent does not match the source texture size.") \
                from error
        if count_length != expected:
            raise TextureSaveError(
                "texture_validation_failed",
                "Lower-mip color intent does not match the source texture size.")
    return counts


def _bc7_weighted_block_intent_info(state, mip, block_index):
    """Classify one lower-mip block's weighted logical intent."""
    source_x, source_y, valid_width, valid_height = _unit_bounds(
        mip, block_index)
    classes = set()
    if state.get("single"):
        changed_counts, total_counts = _bc7_validate_lower_single_state(
            state, mip)
        counts = None
    else:
        counts = _bc7_validate_lower_multi_counts(state, mip)
        changed_counts = total_counts = None
    for row in range(valid_height):
        for column in range(valid_width):
            pixel = ((source_y + row) * mip.width + source_x + column)
            if state["single"]:
                changed = changed_counts[pixel]
                total = total_counts[pixel]
                if (not isinstance(changed, int)
                        or not isinstance(total, int)
                        or changed < 0 or total < 0):
                    raise TextureSaveError(
                        "texture_validation_failed",
                        "Lower-mip color intent contains an invalid count.")
                if changed > total:
                    raise TextureSaveError(
                        "texture_validation_failed",
                        "Changed color intent exceeds total mip weight.")
                if changed:
                    classes.add(1)
                continue
            base_count = counts[0][pixel]
            if (not isinstance(base_count, int) or base_count < 0):
                raise TextureSaveError(
                    "texture_validation_failed",
                    "Lower-mip color intent contains an invalid count.")
            pixel_class_count = 0
            for intent_class, count_values in enumerate(
                    counts[1:], 1):
                count = count_values[pixel]
                if (not isinstance(count, int) or count < 0):
                    raise TextureSaveError(
                        "texture_validation_failed",
                        "Lower-mip color intent contains an invalid count.")
                if count:
                    classes.add(intent_class)
                    pixel_class_count += 1
    if not classes:
        return _BC7BlockIntent(())
    ordered_classes = tuple(sorted(classes))
    return _BC7BlockIntent(ordered_classes)


def _bc7_single_adjustment_target_pixels(
        source_pixels, adjustment, valid_width, valid_height):
    """Apply one Color adjustment while preserving alpha and block padding."""
    target_pixels = list(source_pixels)
    for row in range(valid_height):
        for column in range(valid_width):
            local = row * 4 + column
            rgb = apply_prepared_color_u8(
                source_pixels[local][:3], adjustment)
            target_pixels[local] = rgb + (source_pixels[local][3],)
    return tuple(target_pixels)


def _bc7_weighted_single_target_pixels(
        source_pixels, adjustment, changed_counts, total_counts,
        valid_width, valid_height):
    """Apply weighted intent while preserving alpha and block padding."""
    expected = valid_width * valid_height
    try:
        changed_length = len(changed_counts)
        total_length = len(total_counts)
    except TypeError as error:
        raise TextureSaveError(
            "texture_validation_failed",
            "Weighted BC7 job counts do not match the valid block area.") \
            from error
    if (changed_length != expected or total_length != expected):
        raise TextureSaveError(
            "texture_validation_failed",
            "Weighted BC7 job counts do not match the valid block area.")
    target_pixels = list(source_pixels)
    for row in range(valid_height):
        for column in range(valid_width):
            index = row * valid_width + column
            local = row * 4 + column
            rgb = _bc7_weighted_single_rgb(
                source_pixels[local][:3], adjustment,
                changed_counts[index], total_counts[index])
            target_pixels[local] = rgb + (source_pixels[local][3],)
    return tuple(target_pixels)


def _bc7_target_block_pixels(source_block, mip, block_index, state,
                             adjustments, block_intent=None):
    """Build one sixteen-pixel BC7 target without a reconstructed image."""
    try:
        source_pixels = _bc7_codec.decode_block(source_block)
    except _bc7_codec.BC7Error as error:
        raise TextureSaveError(
            "invalid_bc7", "The texture contains invalid BC7 data.") from error
    source_x, source_y, valid_width, valid_height = _unit_bounds(
        mip, block_index)
    target_pixels = list(source_pixels)
    single_intent_class = None
    if state["level"] == 0:
        if block_intent is None:
            block_intent = _bc7_block_intent_info(
                state, mip, block_index)
        if len(block_intent.classes) == 1:
            single_intent_class = block_intent.classes[0]
    if single_intent_class is not None:
        return (
            source_pixels,
            _bc7_single_adjustment_target_pixels(
                source_pixels, adjustments[single_intent_class],
                valid_width, valid_height),
            valid_width,
            valid_height,
        )
    for row in range(valid_height):
        for column in range(valid_width):
            local = row * 4 + column
            pixel = ((source_y + row) * mip.width + source_x + column)
            rgb = _bc7_intent_rgb(
                source_pixels[local][:3], state, pixel, adjustments)
            target_pixels[local] = rgb + (source_pixels[local][3],)
    return (source_pixels, tuple(target_pixels), valid_width, valid_height)


def _bc7_worker_count():
    """Choose a conservative worker count for CPU-bound BC7 fitting."""
    cpu_count = os.cpu_count() or 1
    return max(1, min(_BC7_MAX_WORKERS, cpu_count - 1))


def _bc7_source_block_and_bounds(original, mip, block_index):
    """Return the one block slice and valid pixel bounds shared by job types."""
    start = mip.offset + block_index * mip.bytes_per_unit
    source_block = bytes(original[start:start + 16])
    _source_x, _source_y, valid_width, valid_height = _unit_bounds(
        mip, block_index)
    return start, source_block, valid_width, valid_height


def _prepare_bc7_block_job(original, mip, block_index, state, adjustments,
                           block_intent):
    """Prepare the parent-owned input sent to one worker."""
    start, source_block, valid_width, valid_height = (
        _bc7_source_block_and_bounds(original, mip, block_index))
    source_pixels, target_pixels, valid_width, valid_height = (
        _bc7_target_block_pixels(
            source_block, mip, block_index, state, adjustments,
            block_intent))
    return _BC7BlockJob(
        start=start, source_block=source_block,
        source_pixels=source_pixels, target_pixels=target_pixels,
        valid_width=valid_width, valid_height=valid_height)


def _prepare_bc7_single_intent_job(
        original, mip, block_index, adjustments, block_intent):
    """Prepare a compact mip-0 job for one explicit Color adjustment."""
    start, source_block, valid_width, valid_height = (
        _bc7_source_block_and_bounds(original, mip, block_index))
    return _BC7SingleIntentJob(
        start=start, source_block=source_block,
        adjustment=adjustments[block_intent.classes[0]],
        valid_width=valid_width, valid_height=valid_height)


def _bc7_single_weighted_block_counts(
        state, mip, block_index, block_intent=None):
    """Extract valid lower-mip weights for one worker job."""
    if state.get("single"):
        changed, total = _bc7_validate_lower_single_state(state, mip)
        changed_values = changed
        total_values = total
    else:
        if (block_intent is None
                or len(block_intent.classes) != 1
                or not isinstance(block_intent.classes[0], int)
                or block_intent.classes[0] <= 0):
            raise TextureSaveError(
                "texture_validation_failed",
                "Lower-mip BC7 block does not have one adjustment class.")
        counts = _bc7_validate_lower_multi_counts(state, mip)
        intent_class = block_intent.classes[0]
        if intent_class >= len(counts):
            raise TextureSaveError(
                "texture_validation_failed",
                "Lower-mip BC7 block contains an invalid adjustment class.")
        changed_values = counts[intent_class]
        base_values = counts[0]
        total_values = None
    source_x, source_y, valid_width, valid_height = _unit_bounds(
        mip, block_index)
    changed_counts = []
    total_counts = []
    for row in range(valid_height):
        start = (source_y + row) * mip.width + source_x
        end = start + valid_width
        if total_values is None:
            for base, changed in zip(
                    base_values[start:end], changed_values[start:end]):
                if (not isinstance(base, int) or not isinstance(changed, int)
                        or base < 0 or changed < 0):
                    raise TextureSaveError(
                        "texture_validation_failed",
                        "Lower-mip color intent contains an invalid count.")
                changed_counts.append(changed)
                total_counts.append(base + changed)
        else:
            for changed, total in zip(
                    changed_values[start:end], total_values[start:end]):
                if (not isinstance(changed, int) or not isinstance(total, int)
                        or changed < 0 or total < 0):
                    raise TextureSaveError(
                        "texture_validation_failed",
                        "Lower-mip color intent contains an invalid count.")
                if changed > total:
                    raise TextureSaveError(
                        "texture_validation_failed",
                        "Changed color intent exceeds total mip weight.")
                changed_counts.append(changed)
                total_counts.append(total)
    return (tuple(changed_counts), tuple(total_counts),
            valid_width, valid_height)


def _prepare_bc7_weighted_single_intent_job(
        original, mip, block_index, state, adjustments, block_intent):
    """Prepare a compact lower-mip job with worker-side target generation."""
    if (len(block_intent.classes) != 1
            or not isinstance(block_intent.classes[0], int)
            or block_intent.classes[0] <= 0
            or block_intent.classes[0] >= len(adjustments)):
        raise TextureSaveError(
            "texture_validation_failed",
            "Lower-mip BC7 block contains an invalid adjustment class.")
    intent_class = block_intent.classes[0]
    start, source_block, valid_width, valid_height = (
        _bc7_source_block_and_bounds(original, mip, block_index))
    changed_counts, total_counts, valid_width, valid_height = (
        _bc7_single_weighted_block_counts(
            state, mip, block_index, block_intent))
    return _BC7WeightedSingleIntentJob(
        start=start, source_block=source_block,
        adjustment=adjustments[intent_class], changed_counts=changed_counts,
        total_counts=total_counts, valid_width=valid_width,
        valid_height=valid_height)


def _prepare_bc7_parallel_job(
        original, mip, block_index, state, adjustments, block_intent):
    """Choose the smallest safe worker input for one BC7 block."""
    if state.get("level") == 0 and len(block_intent.classes) == 1:
        return _prepare_bc7_single_intent_job(
            original, mip, block_index, adjustments, block_intent)
    if state.get("level", 0) > 0 and len(block_intent.classes) == 1:
        return _prepare_bc7_weighted_single_intent_job(
            original, mip, block_index, state, adjustments, block_intent)
    return _prepare_bc7_block_job(
        original, mip, block_index, state, adjustments, block_intent)


def _iter_bc7_jobs(original, mip, affected, state, adjustments,
                    defer_single_intent=False):
    """Yield parent-prepared BC7 jobs."""
    for block_index in affected:
        block_intent = _bc7_block_intent_info(
            state, mip, block_index)
        if defer_single_intent:
            yield _prepare_bc7_parallel_job(
                original, mip, block_index, state, adjustments, block_intent)
        else:
            yield _prepare_bc7_block_job(
                original, mip, block_index, state, adjustments, block_intent)


def _iter_bc7_job_chunks(original, mip, affected, state, adjustments,
                         chunk_size):
    """Group compact jobs into bounded worker submissions."""
    chunk = []
    for job in _iter_bc7_jobs(
            original, mip, affected, state, adjustments,
            defer_single_intent=True):
        chunk.append(job)
        if len(chunk) >= chunk_size:
            yield tuple(chunk)
            chunk = []
    if chunk:
        yield tuple(chunk)


def _recolor_bc7_single_intent(job):
    """Decode, target, and recolor one compact single-intent job."""
    try:
        source_pixels = _bc7_codec.decode_block(job.source_block)
    except _bc7_codec.BC7Error:
        return _BC7BlockResult(
            start=job.start, block=b"", source_error=0,
            candidate_error=0,
            error="The texture contains invalid BC7 data.",
            error_code="invalid_bc7")
    target_pixels = _bc7_single_adjustment_target_pixels(
        source_pixels, job.adjustment, job.valid_width, job.valid_height)
    try:
        result = _bc7_codec.recolor_block(
            job.source_block, target_pixels,
            job.valid_width, job.valid_height, source_pixels)
    except _bc7_codec.BC7Error as error:
        return _BC7BlockResult(
            start=job.start, block=b"", source_error=0,
            candidate_error=0,
            error=str(error))
    return _BC7BlockResult(
        start=job.start, block=result.block,
        source_error=result.source_error,
        candidate_error=result.candidate_error)


def _recolor_bc7_weighted_single_intent(job):
    """Decode, target, and recolor one weighted lower-mip job."""
    try:
        source_pixels = _bc7_codec.decode_block(job.source_block)
    except _bc7_codec.BC7Error:
        return _BC7BlockResult(
            start=job.start, block=b"", source_error=0,
            candidate_error=0,
            error="The texture contains invalid BC7 data.",
            error_code="invalid_bc7")
    try:
        target_pixels = _bc7_weighted_single_target_pixels(
            source_pixels, job.adjustment, job.changed_counts,
            job.total_counts, job.valid_width, job.valid_height)
    except TextureSaveError as error:
        return _BC7BlockResult(
            start=job.start, block=b"", source_error=0,
            candidate_error=0,
            error=error.message, error_code=error.code)
    try:
        result = _bc7_codec.recolor_block(
            job.source_block, target_pixels,
            job.valid_width, job.valid_height, source_pixels)
    except _bc7_codec.BC7Error as error:
        return _BC7BlockResult(
            start=job.start, block=b"", source_error=0,
            candidate_error=0,
            error=str(error))
    return _BC7BlockResult(
        start=job.start, block=result.block,
        source_error=result.source_error,
        candidate_error=result.candidate_error)


def _recolor_bc7_chunk(jobs):
    """Fit one compact chunk in a spawned worker process."""
    results = []
    for job in jobs:
        if isinstance(job, _BC7SingleIntentJob):
            result = _recolor_bc7_single_intent(job)
            results.append(result)
            if result.error is not None:
                break
            continue
        if isinstance(job, _BC7WeightedSingleIntentJob):
            result = _recolor_bc7_weighted_single_intent(job)
            results.append(result)
            if result.error is not None:
                break
            continue
        try:
            result = _bc7_codec.recolor_block(
                job.source_block, job.target_pixels,
                job.valid_width, job.valid_height, job.source_pixels)
        except _bc7_codec.BC7Error as error:
            results.append(_BC7BlockResult(
                start=job.start, block=b"", source_error=0,
                candidate_error=0,
                error=str(error)))
            break
        results.append(_BC7BlockResult(
            start=job.start, block=result.block,
            source_error=result.source_error,
            candidate_error=result.candidate_error))
    return tuple(results)


def _record_bc7_result(final, result, totals):
    """Apply one worker result and retain only safety-gate aggregates."""
    if result.error is not None:
        raise TextureSaveError(
            getattr(result, "error_code", None)
            or "texture_validation_failed", result.error)
    final[result.start:result.start + 16] = result.block
    totals["touched"] += 1
    totals["source_error"] += result.source_error
    totals["final_error"] += result.candidate_error


def _process_bc7_serial(final, original, mip, affected, state, adjustments,
                        totals, record_progress=None):
    """Fit BC7 jobs serially through the same job/result path."""
    for job in _iter_bc7_jobs(
            original, mip, affected, state, adjustments):
        results = _recolor_bc7_chunk((job,))
        for result in results:
            _record_bc7_result(final, result, totals)
            if record_progress is not None:
                record_progress()


def _process_bc7_parallel(final, original, mip, affected, state, adjustments,
                          totals, executor, worker_count, record_progress=None):
    """Fit BC7 jobs with bounded outstanding work on a reused process pool."""
    chunks = iter(_iter_bc7_job_chunks(
        original, mip, affected, state, adjustments, _BC7_CHUNK_SIZE))
    pending = set()
    max_pending = max(1, worker_count * _BC7_MAX_IN_FLIGHT_FACTOR)
    exhausted = False

    def submit_available():
        nonlocal exhausted
        while not exhausted and len(pending) < max_pending:
            try:
                chunk = next(chunks)
            except StopIteration:
                exhausted = True
                break
            try:
                future = executor.submit(_recolor_bc7_chunk, chunk)
            except Exception as error:
                raise TextureSaveError(
                    "texture_processing_failed",
                    "A BC7 worker could not accept texture work.") from error
            pending.add(future)

    submit_available()
    while pending:
        done, _ = wait(pending, return_when=FIRST_COMPLETED)
        for future in done:
            pending.remove(future)
            try:
                results = future.result()
            except Exception as error:
                _LOGGER.exception("BC7 worker failed during texture save")
                raise TextureSaveError(
                    "texture_processing_failed",
                    "A BC7 worker failed during texture save.") from error
            for result in results:
                _record_bc7_result(
                    final, result, totals)
                if record_progress is not None:
                    record_progress()
        submit_available()


def _save_bc7_blocks(original, prepared, progress_reporter=None):
    """Edit authorized BC7 blocks in-place while preserving all other bytes."""
    if not prepared.info.format.startswith("bc7"):
        raise TextureSaveError(
            "texture_validation_failed", "Direct BC7 Save received another format.")
    if len(original) < prepared.layout.payload_end:
        raise TextureSaveError(
            "texture_validation_failed", "DDS payload layout is invalid.")
    raw_adjustments = getattr(prepared, "intent_adjustments", None)
    if not raw_adjustments:
        raise TextureSaveError(
            "texture_validation_failed", "Color intent is missing.")
    adjustments = tuple(
        None if adjustment is None
        else adjustment if isinstance(adjustment, PreparedColorAdjustment)
        else prepare_color_adjustment(adjustment)
        for adjustment in raw_adjustments)
    final = bytearray(original)
    state = _bc7_intent_level(prepared)
    totals = {"touched": 0, "source_error": 0, "final_error": 0}
    worker_count = _bc7_worker_count()
    executor = None
    try:
        for level, mip in enumerate(prepared.layout.mips):
            if level:
                state = _bc7_next_intent_level(
                    state, mip.width, mip.height, len(adjustments))
            affected = state["affected_blocks"]
            if affected is None:
                affected = _bc7_affected_blocks(
                    state["changed_counts"], mip.width, mip.height, mip)
            affected = tuple(affected)
            if progress_reporter is not None:
                progress_reporter.processing(
                    level, len(prepared.layout.mips), 0, len(affected))
            mip_completed = 0

            def record_progress():
                nonlocal mip_completed
                mip_completed += 1
                progress_reporter.processing(
                    level, len(prepared.layout.mips), mip_completed,
                    len(affected))

            use_parallel = (
                len(affected) >= _BC7_PARALLEL_THRESHOLD
                and worker_count > 1)
            if use_parallel:
                if executor is None:
                    try:
                        executor = ProcessPoolExecutor(
                            max_workers=worker_count,
                            mp_context=multiprocessing.get_context("spawn"))
                    except Exception as error:
                        raise TextureSaveError(
                            "texture_processing_failed",
                            "The BC7 worker pool could not be started.") \
                            from error
                _process_bc7_parallel(
                    final, original, mip, affected, state, adjustments,
                    totals, executor, worker_count,
                    record_progress if progress_reporter is not None else None)
            else:
                _process_bc7_serial(
                    final, original, mip, affected, state, adjustments,
                    totals,
                    record_progress if progress_reporter is not None else None)
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
    if not totals["touched"]:
        raise TextureSaveError(
            "incompatible_texture_color_usage",
            "The changed meshes have no writable texture units.",
            "unsupported")
    if totals["final_error"] >= totals["source_error"]:
        raise TextureSaveError(
            "texture_color_not_representable",
            "The requested Color change could not be represented safely in "
            "the source BC7 blocks.", "unsupported")
    return bytes(final)
