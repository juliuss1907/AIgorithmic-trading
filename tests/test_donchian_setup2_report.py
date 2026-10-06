from scripts.verify_donchian_setup2_report import readable_report


def test_table_group_ids_are_escaped_and_charts_resolve_from_audit():
    text = '| SOLUSDT/perp|volume_ratio|(1.2,2] | 31 |\n![R](net-r-distribution.png)'
    groups = [dict(group_id='SOLUSDT/perp|volume_ratio|(1.2,2]')]
    rendered = readable_report(text, groups)
    assert rendered.splitlines()[0] == r'| SOLUSDT/perp\|volume_ratio\|(1.2,2] | 31 |'
    assert '../net-r-distribution.png' in rendered
