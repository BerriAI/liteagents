"""One MCP configuration for Python and managed native adapters."""

from typing import Any


def normalize_servers(servers: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    normalized = {}
    for name, original in servers.items():
        config = dict(original)
        for native, shared in (("http_headers", "headers"), ("enabled_tools", "allowed_tools")):
            if native in config:
                if shared in config and config[shared] != config[native]:
                    raise ValueError(f"MCP {name}: conflicting {shared} and {native}")
                config[shared] = config.pop(native)
        unknown = config.keys() - {
            "url", "command", "args", "env", "headers", "transport", "allowed_tools"
        }
        if unknown:
            raise ValueError(f"MCP {name}: unsupported shared settings {sorted(unknown)}")
        for key in ("headers", "env"):
            if key in config and (
                not isinstance(config[key], dict)
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in config[key].items())
            ):
                raise ValueError(f"MCP {name}: {key} must map strings to strings")
        if "allowed_tools" in config and (
            not isinstance(config["allowed_tools"], list)
            or not all(isinstance(tool, str) and tool.strip() for tool in config["allowed_tools"])
            or len(set(config["allowed_tools"])) != len(config["allowed_tools"])
        ):
            raise ValueError(f"MCP {name}: allowed_tools must be a list of unique tool names")
        if "command" in config:
            if "headers" in config or config.get("transport", "stdio") != "stdio":
                raise ValueError(f"MCP {name}: command servers use stdio and env, not HTTP headers")
        elif "url" in config:
            if config.get("transport", "http") not in ("http", "sse"):
                raise ValueError(f"MCP {name}: URL transport must be http or sse")
            if "env" in config or "args" in config:
                raise ValueError(f"MCP {name}: URL servers use headers, not process args/env")
        normalized[name] = config
    return normalized
