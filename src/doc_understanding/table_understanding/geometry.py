"""Small deterministic geometry helpers, no text interpretation."""

from doc_understanding.domain.models import BBox


def bbox(x0, y0, x1, y1):
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def overlap(a, b):
    return max(0, min(a.x1, b.x1) - max(a.x0, b.x0)) * max(0, min(a.y1, b.y1) - max(a.y0, b.y0))


def close_box(a, b, tolerance):
    return all(abs(x - y) <= tolerance for x, y in zip(a.xyxy, b.xyxy))


def cluster(values, tolerance):
    groups = []
    for value in sorted(values):
        if groups and value - groups[-1][0] <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return tuple(sum(g) / len(g) for g in groups)


def intervals_union(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged
