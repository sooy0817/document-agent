# 역할: 벡터 및 픽셀 근거에서 수평·수직 선을 찾으며 문서명이나 셀 내용은 사용하지 않는다.
"""Axis-aligned vector and raster run detectors. Text labels are never inspected."""

from dataclasses import dataclass

from doc_understanding.domain.models import PageIR, SourceRef
from doc_understanding.parser.physical.geometry import bbox
from doc_understanding.parser.physical.physical_models import GridPolicy, LineSegment


# 페이지 이미지의 회색조 픽셀과 페이지→픽셀 좌표 변환을 묶는다.
@dataclass(frozen=True)
class RasterEvidence:
    width: int
    height: int
    gray_samples: bytes
    # This adapter accepts only the Phase 1 unrotated axis-aligned page transform.
    page_to_pixel: tuple[float, float, float, float, float, float]


# 원본 벡터 명령 또는 이미지에서 수평·수직 선 근거를 추출한다.
class LineDetector:
    # 선 길이·좌표 오차·픽셀 판정에 사용할 GridPolicy를 보관한다.
    def __init__(self, policy: GridPolicy):
        self.policy = policy

    # 그리기 명령 중 실제 stroke의 수평·수직 구간을 수집한다. 채움만 있는 도형은 제외한다.
    def vector(self, page: PageIR) -> tuple[LineSegment, ...]:
        result = []
        for block in page.blocks:
            if block.kind != "drawing":
                continue
            if getattr(block, "drawing_stroked", None) is False:
                continue
            for ci, command in enumerate(block.drawing_commands):
                p = command.coordinates
                if command.operator == "l" and len(p) >= 4:
                    pairs = [(p[0], p[1], p[2], p[3])]
                elif command.operator == "re" and len(p) >= 4:
                    x0, y0, x1, y1 = p[:4]
                    pairs = [(x0, y0, x1, y0), (x1, y0, x1, y1), (x0, y1, x1, y1), (x0, y0, x0, y1)]
                elif command.operator == "qu" and len(p) >= 8:
                    pairs = [
                        (p[0], p[1], p[2], p[3]),
                        (p[2], p[3], p[6], p[7]),
                        (p[6], p[7], p[4], p[5]),
                        (p[4], p[5], p[0], p[1]),
                    ]
                else:
                    continue
                for ei, (x0, y0, x1, y1) in enumerate(pairs):
                    axis = (
                        "horizontal"
                        if abs(y1 - y0) <= self.policy.tolerance
                        else "vertical"
                        if abs(x1 - x0) <= self.policy.tolerance
                        else None
                    )
                    if axis is None:
                        continue
                    position = (y0 + y1) / 2 if axis == "horizontal" else (x0 + x1) / 2
                    start, end = sorted((x0, x1) if axis == "horizontal" else (y0, y1))
                    if end - start < self.policy.min_line_length:
                        continue
                    result.append(
                        LineSegment(
                            line_id=f"{block.block_id}/line{ci}-{ei}",
                            axis=axis,
                            position=position,
                            start=start,
                            end=end,
                            source=block.source,
                            method="vector",
                        )
                    )
        return tuple(result)

    # 회색조 픽셀의 연속 구간을 탐지해 페이지 좌표의 선으로 반환한다. 지원하지 않는 변환은 거부한다.
    def raster(self, page: PageIR, image: RasterEvidence) -> tuple[LineSegment, ...]:
        a, b, c, d, e, f = image.page_to_pixel
        if b or c or a <= 0 or d <= 0 or len(image.gray_samples) != image.width * image.height:
            raise ValueError("unsupported raster transform or pixel buffer")
        result = []
        # Record contiguous dark runs, never heal a missing section of a line.
        for axis, outer, inner in [
            ("horizontal", image.height, image.width),
            ("vertical", image.width, image.height),
        ]:
            min_pixels = max(
                2, int(self.policy.min_line_length * (a if axis == "horizontal" else d))
            )
            for fixed in range(outer):
                run_start = None
                for varying in range(inner + 1):
                    offset = (
                        fixed * image.width + varying
                        if axis == "horizontal"
                        else varying * image.width + fixed
                    )
                    dark = (
                        varying < inner
                        and image.gray_samples[offset] <= self.policy.raster_threshold
                    )
                    if varying < inner and self.policy.raster_strategy != "legacy_dark":
                        # A narrow ridge must contrast with BOTH perpendicular neighbours.
                        # Uniform dark fills and their one-sided edges are not grid strokes.
                        radius = max(2, round((d if axis == "horizontal" else a) * 1.2))
                        before, after = fixed - radius, fixed + radius
                        dark = False
                        if before >= 0 and after < outer:
                            left = (
                                before * image.width + varying
                                if axis == "horizontal"
                                else varying * image.width + before
                            )
                            right = (
                                after * image.width + varying
                                if axis == "horizontal"
                                else varying * image.width + after
                            )
                            value = image.gray_samples[offset]
                            lo = min(image.gray_samples[left], image.gray_samples[right])
                            hi = max(image.gray_samples[left], image.gray_samples[right])
                            dark = value + self.policy.raster_contrast <= lo
                            if self.policy.raster_strategy == "adaptive_contrast":
                                dark = dark or value - self.policy.raster_contrast >= hi
                    if dark and run_start is None:
                        run_start = varying
                    if not dark and run_start is not None:
                        if varying - run_start >= min_pixels:
                            pos = (
                                (fixed + 0.5 - f) / d
                                if axis == "horizontal"
                                else (fixed + 0.5 - e) / a
                            )
                            start = (
                                (run_start - e) / a if axis == "horizontal" else (run_start - f) / d
                            )
                            end = (varying - e) / a if axis == "horizontal" else (varying - f) / d
                            sb = (
                                bbox(start, pos, end, pos)
                                if axis == "horizontal"
                                else bbox(pos, start, pos, end)
                            )
                            result.append(
                                LineSegment(
                                    line_id=f"{page.page_id}/raster/{axis}/{fixed}/{run_start}",
                                    axis=axis,
                                    position=pos,
                                    start=start,
                                    end=end,
                                    source=SourceRef(
                                        document_id=page.page_id.rsplit("/p", 1)[0],
                                        page_number=page.page_number,
                                        bbox=sb,
                                    ),
                                    method="raster",
                                )
                            )
                        run_start = None
        if self.policy.raster_strategy == "adaptive_contrast":
            result.extend(self._fill_transitions(page, image))
        return tuple(result)

    # 긴 채움 띠의 명암 전이를 별도 경계 후보로 추출한다. 실제 stroke와 구분해 추론 상태를 유지한다.
    def _fill_transitions(self, page, image):
        """Long dark-band transitions are layout evidence, not observed strokes.

        Retain the distinction so cells touching these boundaries remain inferred.
        A transition must run over 35% of page width, allowing only 2-pixel holes.
        """
        a, _, _, d, e, f = image.page_to_pixel
        width, height = image.width, image.height
        pixels = image.gray_samples
        result = []
        for y in range(4, height - 4):
            hits = [
                x
                for x in range(width)
                if abs(pixels[(y - 1) * width + x] - pixels[(y + 1) * width + x]) >= 55
                and min(pixels[(y - 1) * width + x], pixels[(y + 1) * width + x]) < 180
                and (
                    (pixels[(y - 1) * width + x] < 180 and pixels[(y - 4) * width + x] < 180)
                    or (pixels[(y + 1) * width + x] < 180 and pixels[(y + 4) * width + x] < 180)
                )
            ]
            if len(hits) < width * 0.35:
                continue
            groups = []
            for x in hits:
                if groups and x - groups[-1][-1] <= 3:
                    groups[-1].append(x)
                else:
                    groups.append([x])
            for group in groups:
                if (
                    group[-1] - group[0] < width * 0.35
                    or len(group) / (group[-1] - group[0] + 1) < 0.95
                ):
                    continue
                pos = (y + 0.5 - f) / d
                start, end = (group[0] - e) / a, (group[-1] + 1 - e) / a
                result.append(
                    LineSegment(
                        line_id=f"{page.page_id}/raster/fill-transition/{y}/{group[0]}",
                        axis="horizontal",
                        position=pos,
                        start=start,
                        end=end,
                        source=SourceRef(
                            document_id=page.page_id.rsplit("/p", 1)[0],
                            page_number=page.page_number,
                            bbox=bbox(start, pos, end, pos),
                        ),
                        method="raster",
                        evidence_kind="fill_transition",
                    )
                )
        return result
