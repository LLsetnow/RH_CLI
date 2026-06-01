from __future__ import annotations

from rh_cli.app.nodes import parse_node_arg, parse_webapp_id


def test_parse_webapp_id_from_url():
    assert parse_webapp_id("https://www.runninghub.cn/ai-detail/1877265245566922800") == "1877265245566922800"


def test_parse_node_arg():
    assert parse_node_arg("52:prompt=a girl dancing") == ("52", "prompt", "a girl dancing")
