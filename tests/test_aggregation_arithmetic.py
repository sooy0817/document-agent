from doc_understanding.validation.arithmetic import compare_sum, parse_amount


def test_exact_decimal_sum_and_mismatch():
    assert compare_sum(['1,000.1', '0.2'], '1,000.3')['status']=='match'
    assert compare_sum(['10', '-2'], '9')['difference']=='-1'


def test_unknowns_are_not_zero():
    for value in ['', '-', '△10', '10%', '1,23', None]:
        assert parse_amount(value) is None
        assert compare_sum(['1', value], '1')['status']=='unresolved'
    assert compare_sum([], '0')['status']=='unresolved'


def test_explicit_dash_policy_preserves_blank_and_negative_semantics():
    assert compare_sum(['10', '-', '-2'], '8', dash_as_zero=True)['status']=='match'
    assert compare_sum(['10', ''], '10', dash_as_zero=True)['status']=='unresolved'
    assert compare_sum(['10', None], '10', dash_as_zero=True)['status']=='unresolved'
    assert compare_sum(['-'], '-', dash_as_zero=True)['status']=='match'
    assert parse_amount('-') is None


def test_blank_policy_does_not_mask_missing_source_or_mismatch():
    assert compare_sum(['10', ' '], '10', blank_as_zero=True)['status']=='match'
    assert compare_sum(['10', ''], '11', blank_as_zero=True)['status']=='mismatch'
    assert compare_sum(['10', None], '10', blank_as_zero=True)['status']=='unresolved'
    assert parse_amount('') is None
    assert parse_amount('10%', blank_as_zero=True) is None
