from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .server import Server


class Link(BaseModel):
    """
    The `Link object` represents a possible design-time link for a response.
    The presence of a link does not guarantee the caller's ability to successfully invoke it,
    rather it provides a known relationship and traversal mechanism between responses and other operations.

    Unlike _dynamic_ links (i.e. links provided **in** the response payload),
    the OAS linking mechanism does not require link information in the runtime response.

    For computing links, and providing instructions to execute them,
    a [runtime expression](#runtimeExpression) is used for accessing values in an operation
    and using them as params while invoking the linked operation.

    References:
        - https://swagger.io/docs/specification/links/
        - https://github.com/OAI/OpenAPI-Specification/blob/master/versions/3.0.3.md#linkObject

    """

    operationRef: str | None = None
    operationId: str | None = None
    params: dict[str, Any] | None = Field(default=None, alias="parameters")
    requestBody: Any | None = None
    description: str | None = None
    server: Server | None = None
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {"operationId": "getUserAddressByUUID", "parameters": {"userUuid": "$response.body#/uuid"}},
                {
                    "operationRef": "#/paths/~12.0~1repositories~1{username}/get",
                    "parameters": {"username": "$response.body#/username"},
                },
            ]
        },
    )
