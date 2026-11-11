"""Exact-position triangle connectivity shared with the loose-parts UI contract."""


def find_loose_part_groups(triangles, position_keys):
    """Return original triangle ordinals, ordered like zero-tolerance separation."""
    if len(triangles) < 2:
        return []
    parents = list(range(len(triangles)))
    ranks = bytearray(len(triangles))

    def find(value):
        root = value
        while parents[root] != root:
            root = parents[root]
        while parents[value] != value:
            following = parents[value]
            parents[value] = root
            value = following
        return root

    def union(left, right):
        left, right = find(left), find(right)
        if left == right:
            return
        if ranks[left] < ranks[right]:
            left, right = right, left
        parents[right] = left
        if ranks[left] == ranks[right]:
            ranks[left] += 1

    first_triangle = {}
    for ordinal, triangle in enumerate(triangles):
        for vertex in triangle:
            if vertex < 0 or vertex >= len(position_keys):
                return []
            key = position_keys[vertex]
            previous = first_triangle.setdefault(key, ordinal)
            union(ordinal, previous)
    groups = {}
    for ordinal in range(len(triangles)):
        groups.setdefault(find(ordinal), []).append(ordinal)
    return list(groups.values())
