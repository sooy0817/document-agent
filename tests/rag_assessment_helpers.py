"""명시적 모의 응답 생성기. 모델의 의미 이해 성능을 평가하는 도구가 아니다."""


def receipt(question, sources=(), *, status="supported", external=False):
    supports = []
    for source in sources:
        for field in ("row_path", "column_path", "text_raw"):
            values = source.get(field, [])
            values = [values] if isinstance(values, str) else values
            supports.extend(
                dict(source_id=source["source_id"], field=field, quote=v)
                for v in values
                if v and v.strip()
            )
    return dict(
        question=question,
        status=status,
        requires_external_verification=external,
        requirements=[
            dict(
                question_quote=question,
                requirement="요청 내용의 근거 확인",
                status=status,
                supports=supports,
            )
        ],
        explanation="모의 응답",
    )
