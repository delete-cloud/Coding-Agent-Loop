"""JSON aliases used by runtime records and codecs."""

from __future__ import annotations

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]
