# 역할: 가늘고 긴 채움 직사각형의 중심선을 기본 벡터 선 근거에 보완한다. 기본 stroke_fill 경로에서 사용한다.
"""Opt-in PDF fill-rule adapter. Does not modify any serialized IR contract.

The existing fill_transition evidence category conservatively keeps these
centerline hypotheses inferred. Original rectangle coordinates remain in
DocumentIR; line IDs identify the exact drawing command. No text is inspected.
"""
from doc_understanding.parser.physical.line_detector import LineDetector
from doc_understanding.parser.physical.physical_models import LineSegment


# 일반 stroke 탐지기에 제한된 채움 도형의 중심선 후보를 추가한다.
class FillBoundaryDetector(LineDetector):
    # 기본 벡터 stroke와 채움 중심선 후보를 합치되 각각의 근거 종류는 보존한다.
    def vector(self, page):
        return super().vector(page) + self.fill_lines(page)

    # 허용 두께·길이를 만족하는 채움 직사각형만 선으로 변환하고 fill_transition으로 표시한다.
    def fill_lines(self, page):
        lines = []
        for block in page.blocks:
            if block.kind != 'drawing' or block.drawing_stroked is not False:
                continue
            for index, command in enumerate(block.drawing_commands):
                # Do not invent straight sides of arbitrary polygons/curves.
                if command.operator != 're' or len(command.coordinates) < 4:
                    continue
                x0,y0,x1,y1 = command.coordinates[:4]
                x0,x1 = sorted((x0,x1)); y0,y1 = sorted((y0,y1))
                width,height = x1-x0,y1-y0
                thickness,length = min(width,height),max(width,height)
                # Geometry-only fixed policy. Keep short actual fragments;
                # existing grid coverage tests decide whether they form edges.
                if not (0 < thickness <= 2*self.policy.tolerance
                        and length >= max(2*self.policy.tolerance,6*thickness)):
                    continue
                horizontal = width > height
                lines.append(LineSegment(
                    line_id=f'{block.block_id}/fill-center/{index}',
                    axis='horizontal' if horizontal else 'vertical',
                    position=(y0+y1)/2 if horizontal else (x0+x1)/2,
                    start=x0 if horizontal else y0, end=x1 if horizontal else y1,
                    source=block.source, method='vector', evidence_kind='fill_transition'))
        return tuple(lines)
