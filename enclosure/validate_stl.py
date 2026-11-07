#!/usr/bin/env python3
"""Small dependency-free binary-STL topology check for the enclosure exports."""

from __future__ import annotations

import argparse
import struct
from collections import defaultdict
from pathlib import Path


def vertex_key(vertex: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(round(value, 5) for value in vertex)


def inspect(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    if len(data) < 84:
        raise ValueError("file is too short to be a binary STL")

    triangle_count = struct.unpack_from("<I", data, 80)[0]
    expected_size = 84 + triangle_count * 50
    if len(data) != expected_size:
        raise ValueError(
            f"binary STL size mismatch: expected {expected_size}, got {len(data)}"
        )

    parent = list(range(triangle_count))
    rank = [0] * triangle_count

    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        if rank[left_root] < rank[right_root]:
            left_root, right_root = right_root, left_root
        parent[right_root] = left_root
        if rank[left_root] == rank[right_root]:
            rank[left_root] += 1

    edges: dict[
        tuple[tuple[float, float, float], tuple[float, float, float]], list[int]
    ] = defaultdict(list)
    triangles: list[list[tuple[float, float, float]]] = []
    mins = [float("inf")] * 3
    maxs = [float("-inf")] * 3
    degenerate = 0

    offset = 84
    for triangle_index in range(triangle_count):
        values = struct.unpack_from("<12fH", data, offset)
        offset += 50
        vertices = [
            vertex_key((values[3], values[4], values[5])),
            vertex_key((values[6], values[7], values[8])),
            vertex_key((values[9], values[10], values[11])),
        ]
        triangles.append(vertices)
        for vertex in vertices:
            for axis, value in enumerate(vertex):
                mins[axis] = min(mins[axis], value)
                maxs[axis] = max(maxs[axis], value)
        if len(set(vertices)) < 3:
            degenerate += 1
        for index in range(3):
            edge = tuple(sorted((vertices[index], vertices[(index + 1) % 3])))
            edges[edge].append(triangle_index)

    for owners in edges.values():
        for owner in owners[1:]:
            union(owners[0], owner)

    component_roots = {find(index) for index in range(triangle_count)}
    components = len(component_roots)
    boundary_edges = sum(len(owners) == 1 for owners in edges.values())
    nonmanifold_edges = sum(len(owners) != 2 for owners in edges.values())
    size = [round(maxs[i] - mins[i], 3) for i in range(3)]

    result: dict[str, object] = {
        "triangles": triangle_count,
        "components": components,
        "boundary_edges": boundary_edges,
        "nonmanifold_edges": nonmanifold_edges,
        "degenerate_triangles": degenerate,
        "size": size,
        "min": [round(value, 3) for value in mins],
        "max": [round(value, 3) for value in maxs],
    }
    if components > 1:
        component_data: dict[int, dict[str, object]] = {}
        for index, vertices in enumerate(triangles):
            root = find(index)
            item = component_data.setdefault(
                root,
                {
                    "triangles": 0,
                    "min": [float("inf")] * 3,
                    "max": [float("-inf")] * 3,
                },
            )
            item["triangles"] += 1
            for vertex in vertices:
                for axis, value in enumerate(vertex):
                    item["min"][axis] = min(item["min"][axis], value)
                    item["max"][axis] = max(item["max"][axis], value)
        result["component_details"] = sorted(
            (
                {
                    "triangles": item["triangles"],
                    "min": [round(value, 3) for value in item["min"]],
                    "max": [round(value, 3) for value in item["max"]],
                }
                for item in component_data.values()
            ),
            key=lambda item: item["triangles"],
            reverse=True,
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stl", nargs="+", type=Path)
    args = parser.parse_args()
    failed = False
    for path in args.stl:
        try:
            result = inspect(path)
        except (OSError, ValueError) as error:
            failed = True
            print(f"FAIL {path}: {error}")
            continue
        okay = (
            result["components"] == 1
            and result["nonmanifold_edges"] == 0
            and result["degenerate_triangles"] == 0
        )
        failed |= not okay
        status = "PASS" if okay else "FAIL"
        print(
            f"{status} {path}: {result['triangles']} triangles, "
            f"{result['components']} shell, {result['boundary_edges']} boundary edges, "
            f"{result['nonmanifold_edges']} nonmanifold edges, "
            f"{result['degenerate_triangles']} degenerate, size {result['size']} mm"
        )
        for component in result.get("component_details", []):
            print(
                f"  component: {component['triangles']} triangles, "
                f"min {component['min']}, max {component['max']}"
            )
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
