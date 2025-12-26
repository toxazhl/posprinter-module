from typing import Any, Union

import yaml
from pydantic import TypeAdapter

# Імпорти моделей
from posprinter.models import (
    CheckStatusResponse,
    ErrorResponse,
    GetPrintersResponse,
    RequestModel,
    SuccessResponse,
)

# Union відповідей
ResponseModel = Union[
    GetPrintersResponse, CheckStatusResponse, ErrorResponse, SuccessResponse
]


def sanitize_schema(obj: Any) -> Any:
    """
    Рекурсивно чистить схему від Pydantic-специфічних полів,
    які ламають AsyncAPI валідатори (зокрема discriminator).
    """
    if isinstance(obj, dict):
        # 1. Видаляємо discriminator, бо Pydantic робить його об'єктом,
        # а AsyncAPI часто хоче string або інший формат.
        # Це головна причина помилки "discriminator property type must be string".
        if "discriminator" in obj:
            del obj["discriminator"]

        # 2. Видаляємо title, якщо він просто дублює назву (не обов'язково, але чистіше)
        # if "title" in obj:
        #    del obj["title"]

        # Рекурсія по словнику
        for key, value in obj.items():
            obj[key] = sanitize_schema(value)

    elif isinstance(obj, list):
        # Рекурсія по списку
        return [sanitize_schema(item) for item in obj]

    return obj


def generate_asyncapi():
    print("Генерую AsyncAPI (Sanitized Mode)...")

    req_adapter = TypeAdapter(RequestModel)
    res_adapter = TypeAdapter(ResponseModel)

    # Генеруємо схеми
    # ref_template веде в components/schemas
    req_schema = req_adapter.json_schema(ref_template="#/components/schemas/{model}")
    res_schema = res_adapter.json_schema(ref_template="#/components/schemas/{model}")

    all_schemas = {}

    # Витягуємо $defs (Definitions)
    if "$defs" in req_schema:
        all_schemas.update(req_schema["$defs"])
        del req_schema["$defs"]

    if "$defs" in res_schema:
        all_schemas.update(res_schema["$defs"])
        del res_schema["$defs"]

    # Додаємо головні моделі в schemas
    all_schemas["RequestModel"] = req_schema
    all_schemas["ResponseModel"] = res_schema

    # !!! НАЙВАЖЛИВІШИЙ ЕТАП: ЧИСТИМО СХЕМИ !!!
    # Це прибере помилки валідації
    clean_schemas = sanitize_schema(all_schemas)

    asyncapi_spec = {
        "asyncapi": "3.0.0",
        "info": {
            "title": "POS Printer Daemon CLI",
            "version": "1.0.0",
            "description": "Clean documentation without strict Pydantic artifacts.",
        },
        "servers": {"cli": {"host": "localhost", "protocol": "stdio"}},
        "channels": {
            "stdin": {
                "address": "stdin",
                "messages": {
                    "request": {"$ref": "#/components/messages/ClientRequest"}
                },
            },
            "stdout": {
                "address": "stdout",
                "messages": {
                    "response": {"$ref": "#/components/messages/ServerResponse"}
                },
            },
        },
        "operations": {
            "sendCommand": {
                "action": "send",
                "channel": {"$ref": "#/channels/stdin"},
                "summary": "Send JSON Command",
                "messages": [{"$ref": "#/channels/stdin/messages/request"}],
            },
            "readResponse": {
                "action": "receive",
                "channel": {"$ref": "#/channels/stdout"},
                "summary": "Read JSON Response",
                "messages": [{"$ref": "#/channels/stdout/messages/response"}],
            },
        },
        "components": {
            "schemas": clean_schemas,
            "messages": {
                "ClientRequest": {
                    "name": "ClientRequest",
                    "title": "Request Payload",
                    "payload": {"$ref": "#/components/schemas/RequestModel"},
                },
                "ServerResponse": {
                    "name": "ServerResponse",
                    "title": "Response Payload",
                    "payload": {"$ref": "#/components/schemas/ResponseModel"},
                },
            },
        },
    }

    with open("asyncapi.yaml", "w", encoding="utf-8") as f:
        yaml.dump(asyncapi_spec, f, allow_unicode=True, sort_keys=False)

    print("Файл asyncapi.yaml готовий. Спробуй тепер, має бути чисто.")


if __name__ == "__main__":
    generate_asyncapi()
