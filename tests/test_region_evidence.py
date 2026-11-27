"""Geometry-only recovery gates, including cases that must stay separate."""
from doc_understanding.domain.models import BBox, SourceRef
from doc_understanding.parser.physical.physical_models import GridPolicy, LineSegment
from doc_understanding.parser.physical.region_detector import TableRegionDetector


def line(axis, pos, start, end):
    return LineSegment(line_id=f'{axis}/{pos}/{start}/{end}',axis=axis,position=pos,start=start,end=end,
        method='vector',source=SourceRef(document_id='synthetic',page_number=1,
            bbox=BBox(x0=start,y0=pos,x1=end,y1=pos) if axis=='horizontal'
            else BBox(x0=pos,y0=start,x1=pos,y1=end)))


def fragment(y0,y1):
    return tuple([line('horizontal',y,10,210) for y in [y0,y1]]+
                 [line('vertical',x,y0,y1) for x in [10,110,210]])


def test_open_sides_have_repeated_endpoints_and_an_internal_divider():
    lines=tuple([line('horizontal',y,10,210) for y in [10,30,50,70]]+
                [line('vertical',110,10,70)])
    regions=TableRegionDetector(GridPolicy()).detect(lines)
    assert len(regions)==1
    assert regions[0].bounding_box==BBox(x0=10,y0=10,x1=210,y1=70)
    # No fabricated side strokes; the existing grid stage must mark inference.
    assert regions[0].lines==lines


def test_open_sides_without_repeated_extent_are_not_admitted():
    lines=tuple([line('horizontal',y,10+i*5,210-i*5) for i,y in enumerate([10,30,50,70])]+
                [line('vertical',110,10,70)])
    assert not TableRegionDetector(GridPolicy()).detect(lines)


def test_source_enclosing_hint_and_aligned_profiles_join_small_gap():
    lines=fragment(10,30)+fragment(31.2,70)
    hint=BBox(x0=10,y0=10,x1=210,y1=70)
    assert len(TableRegionDetector(GridPolicy()).detect(lines,(hint,)))==1


def test_nearby_tables_without_enclosing_hint_stay_separate():
    assert len(TableRegionDetector(GridPolicy()).detect(fragment(10,30)+fragment(31.2,70)))==2


def test_enclosing_hint_does_not_bridge_large_gap():
    hint=BBox(x0=10,y0=10,x1=210,y1=70)
    assert len(TableRegionDetector(GridPolicy()).detect(fragment(10,30)+fragment(40,70),(hint,)))==2


def test_enclosing_hint_does_not_join_different_column_profiles():
    lower=tuple(l.model_copy(update={'position':140}) if l.axis=='vertical' and l.position==110 else l
                for l in fragment(31.2,70))
    hint=BBox(x0=10,y0=10,x1=210,y1=70)
    assert len(TableRegionDetector(GridPolicy()).detect(fragment(10,30)+lower,(hint,)))==2


def test_text_in_the_gap_blocks_fragment_join():
    from types import SimpleNamespace as NS
    hint=BBox(x0=10,y0=10,x1=210,y1=70)
    span=NS(text_raw='separate section',source=NS(bbox=BBox(x0=30,y0=30,x1=150,y1=31.2)))
    assert len(TableRegionDetector(GridPolicy()).detect(fragment(10,30)+fragment(31.2,70),(hint,),(span,)))==2


def test_native_padding_partition_requires_geometry_and_span_agreement():
    from types import SimpleNamespace as NS
    from doc_understanding.parser.physical.nested_regions import native_partition_support
    b=lambda a,z:BBox(x0=a,y0=10,x1=z,y1=30)
    parts=[NS(cell_id=f'n{i}',source=NS(bbox=b(a,z)),span_refs=refs)
           for i,(a,z,refs) in enumerate([(10,15,()),(15,100,('s',)),(100,110,())])]
    native=NS(cells=parts)
    physical=NS(cells=[NS(cell_id='p',source_bbox=b(10,110),span_refs=('s',))])
    assert native_partition_support(native,physical,{'s'},.75)
    parts[1].span_refs=()
    assert native_partition_support(native,physical,{'s'},.75) is None
    parts[1].span_refs=('s',)
    parts[1].source.bbox=b(25,100)
    assert native_partition_support(native,physical,{'s'},.75) is None
