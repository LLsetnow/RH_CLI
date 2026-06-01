from __future__ import annotations

from rh_cli.model.payload import ModelRunInput, build_payload, parse_params


class DummyClient:
    api_key = "test"


def test_parse_params_requires_key_value():
    assert parse_params(["a=b", "n=1"]) == {"a": "b", "n": "1"}


def test_build_payload_maps_prompt_and_coerces_params():
    endpoint = {
        "output_type": "image",
        "params": [
            {"key": "prompt", "type": "STRING", "required": True},
            {"key": "num", "type": "INT", "required": False},
            {"key": "enabled", "type": "BOOLEAN", "required": False},
            {"key": "aspectRatio", "type": "LIST", "required": True, "default": "1:1"},
        ],
    }
    payload = build_payload(
        DummyClient(),  # type: ignore[arg-type]
        endpoint,
        ModelRunInput(prompt="a cat", params=["num=2", "enabled=true"]),
    )
    assert payload == {
        "prompt": "a cat",
        "num": 2,
        "enabled": True,
        "aspectRatio": "1:1",
    }
