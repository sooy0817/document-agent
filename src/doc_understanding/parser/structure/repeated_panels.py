# 역할: 반복 헤더와 독립된 본문 배치로 좌우 패널 후보를 찾고 불충분한 근거는 보류한다.
"""Conservative flat repeated-panel candidate; no document/label vocabulary.

This is a structural hypothesis, not proof of independent semantic tables.
Numeric-only stubs, overlapping label sets, merges and incomplete grids abstain.
"""
# 완전한 비병합 격자에서 반복 헤더·독립 행 라벨·본문 완전성을 검사한다.
# 분할 폭이 하나로 결정될 때만 열 구간을 반환하고, 경쟁 해석이 있으면 빈 결과로 보류한다.
def repeated_panel_ranges(table):
    nr,nc=len(table.rows),len(table.columns)
    if nr<3 or nc<4 or len(table.cells)!=nr*nc:
        return ()
    grid={}
    for c in table.cells:
        if len(c.occupied_slots)!=1 or c.rowspan!=1 or c.colspan!=1 or c.status=='unresolved':return ()
        slot=c.occupied_slots[0]
        if slot!=(c.row_index,c.column_index):return ()
        if slot in grid:return ()
        grid[slot]=c
    if set(grid)!={(r,c) for r in range(nr) for c in range(nc)}:return ()
    norm=lambda s:''.join((s or '').split()).casefold()
    header=[norm(grid[0,c].original_text) for c in range(nc)]
    if not all(header):return ()
    viable=[]
    for width in range(2,nc//2+1):
        if nc%width or len(set(header[:width]))!=width:continue
        if any(header[c]!=header[c%width] for c in range(nc)):continue
        labelsets=[];valid=True
        for start in range(0,nc,width):
            labels=[];complete=0
            for row in range(1,nr):
                label=norm(grid[row,start].original_text)
                values=[norm(grid[row,c].original_text) for c in range(start+1,start+width)]
                if not label and any(values):valid=False
                if label:
                    if not any(ch.isalpha() for ch in label):valid=False
                    labels.append(label)
                    if all(values):complete+=1
            if complete<2 or len(set(labels))!=len(labels):valid=False
            labelsets.append(set(labels))
        if any(a&b for i,a in enumerate(labelsets) for b in labelsets[i+1:]):valid=False
        if valid:viable.append(tuple((c,c+width) for c in range(0,nc,width)))
    return viable[0] if len(viable)==1 else ()
