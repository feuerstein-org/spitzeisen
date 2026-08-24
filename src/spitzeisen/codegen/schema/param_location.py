from enum import StrEnum


class ParamLocation(StrEnum):
    """The places params can be put when calling an OpenAPI operation."""

    QUERY = "query"
    PATH = "path"
    HEADER = "header"
    COOKIE = "cookie"
