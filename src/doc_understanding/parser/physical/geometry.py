# 역할: 좌표 겹침·거리·구간 병합 계산을 제공하며 텍스트 의미는 판단하지 않는다.
"""Small deterministic geometry helpers, no text interpretation."""

from doc_understanding.domain.models import BBox


# 네 좌표로 공통 BBox를 만든다. 좌표 체계 변환은 수행하지 않는다.
def bbox(x0, y0, x1, y1):
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


# 두 상자의 교집합 면적을 반환한다. 닿기만 하는 경우 면적은 0이다.
def overlap(a, b):
    return max(0, min(a.x1, b.x1) - max(a.x0, b.x0)) * max(0, min(a.y1, b.y1) - max(a.y0, b.y0))


# 네 모서리 좌표가 각각 허용 오차 이내인지 비교해 같은 셀 후보인지 판단한다.
def close_box(a, b, tolerance):
    return all(abs(x - y) <= tolerance for x, y in zip(a.xyxy, b.xyxy))


# 정렬된 좌표를 그룹 첫 값과의 허용 거리로 묶고 각 그룹의 평균 위치를 반환한다.
def cluster(values, tolerance):
    groups = []
    for value in sorted(values):
        if groups and value - groups[-1][0] <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return tuple(sum(g) / len(g) for g in groups)


# 겹치거나 맞닿은 선 구간을 합쳐 중복 길이 계산을 방지한다.
def intervals_union(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged
