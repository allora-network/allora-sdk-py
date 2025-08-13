#!/usr/bin/env python3
"""
Generate a REST client that returns betterproto2 models from an OpenAPI (Swagger) spec.

This script reads an OpenAPI v3 JSON/YAML spec (e.g., merged allora.swagger.json)
and emits a typed async client that:
  - Calls REST endpoints with aiohttp
  - Converts JSON responses into betterproto2 messages (using a small runtime helper)
  - Optionally filters endpoints by tag (e.g., emissions.v9, mint.v5)

Example:
  python scripts/generate_rest_client_from_openapi.py \
    --spec openapi/allora.swagger.json \
    --out src/allora_sdk/rest/generated_client.py \
    --include-tags emissions.v9 mint.v5

After generation, import and use:
  from allora_sdk.rest.generated_client import AlloraRestClient
  client = AlloraRestClient(base_url="https://allora-api.testnet.allora.network")
  resp = await client.emissions_can_submit_worker_payload(topic_id=13, address="allo1...")

Notes:
  - The generator attempts to map response schemas like "emissions.v9.GetParamsResponse"
    to your betterproto2 modules at "allora_sdk.protobuf_client.proto.emissions.v9".
  - If a schema cannot be mapped, the endpoint is still generated returning raw dict.
  - Request bodies (if any) are accepted as dict; you can enhance mappings over time.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import importlib
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


PROTO_BASE = "allora_sdk.protobuf_client.protos"


def load_spec(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        if yaml is None:
            raise SystemExit("pyyaml not installed; install pyyaml or provide a JSON spec")
        return yaml.safe_load(text)
    return json.loads(text)


def snake(s: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", s)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.strip("_").lower()


def camel(s: str) -> str:
    parts = re.split(r"[^0-9a-zA-Z]+", s)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def guess_proto_import_for_schema(schema_ref: str) -> Optional[Tuple[str, str]]:
    """Map an OpenAPI schema ref to (module_import, class_name) for betterproto2.

    Expects refs like "#/components/schemas/emissions.v9.GetParamsResponse".
    Returns ("allora_sdk.protobuf_client.proto.emissions.v9", "GetParamsResponse").
    """
    if not schema_ref.startswith("#/components/schemas/"):
        return None
    name = schema_ref.split("#/components/schemas/")[-1]
    parts = name.split(".")
    if len(parts) < 3:
        return None
    # e.g., ["emissions", "v9", "GetParamsResponse"]
    pkg, ver, *rest = parts
    class_name = rest[-1]
    module = f"{PROTO_BASE}.{pkg}.{ver}"
    return module, class_name
def fallback_proto_from_tag_and_path(tag: str, path: str) -> Optional[Tuple[str, str, str]]:
    """Derive (module, response_class, request_class) from tag and last path segment.

    For tag like 'mint.v5' and path '/mint/v5/params', we return:
      module='allora_sdk.protobuf_client.protos.mint.v5',
      response='QueryServiceParamsResponse',
      request='QueryServiceParamsRequest'
    """
    if not tag or "." not in tag:
        return None
    pkg, ver = tag.split(".", 1)
    module = f"{PROTO_BASE}.{pkg}.{ver}"
    segs = [seg for seg in path.strip('/').split('/') if seg and not seg.startswith('{')]
    last = segs[-1] if segs else "query"
    method = camel(last)
    # Defer exact class selection to introspection; return module and method
    return module, method + "Response", method + "Request"


def introspect_classes(module_path: str, method: str) -> Tuple[Optional[str], Optional[str]]:
    """Try to find concrete Request/Response class names in module for a method.

    Preference order for Response: {Method}Response, QueryService{Method}Response
    Preference order for Request:  {Method}Request,  QueryService{Method}Request
    """
    try:
        mod = importlib.import_module(module_path)
    except Exception:
        return None, None
    names = set(dir(mod))
    candidates_resp = [f"{method}Response", f"QueryService{method}Response"]
    candidates_req = [f"{method}Request", f"QueryService{method}Request"]
    resp = next((c for c in candidates_resp if c in names), None)
    req = next((c for c in candidates_req if c in names), None)
    return resp, req


def method_from_operation_id(op: Dict[str, Any]) -> Optional[str]:
    op_id = op.get("operationId")
    if not op_id or not isinstance(op_id, str):
        return None
    # Common patterns: "QueryService_GetParams", "MsgService_RecalculateTargetEmission"
    if "_" in op_id:
        suffix = op_id.split("_")[-1]
    else:
        # Some specs may have dot or slash separators
        for sep in ("/", "."):
            if sep in op_id:
                suffix = op_id.split(sep)[-1]
                break
        else:
            suffix = op_id
    # Normalize to CamelCase method name
    return camel(suffix)
def derive_request_class_from_response(resp_class: str) -> Optional[str]:
    if resp_class.endswith("Response"):
        return resp_class[:-8] + "Request"
    if resp_class.endswith("Resp"):
        return resp_class[:-4] + "Request"
    return None

def method_name_from_path(path: str, fallback: str) -> str:
    segs = [s for s in path.strip("/").split("/") if s and not s.startswith("{")]
    # take last non-parameter segment
    name = segs[-1] if segs else fallback
    return snake(name)



def collect_operations(
    spec: Dict[str, Any], include_tags: List[str], include_path_prefix: List[str]
) -> List[Dict[str, Any]]:
    ops: List[Dict[str, Any]] = []
    tag_filters = [t.lower() for t in include_tags]
    path_filters = [p.rstrip("/") for p in include_path_prefix]
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            if method.lower() not in {"get", "post", "put", "delete", "patch"}:
                continue
            tags = [str(t) for t in op.get("tags", [])]
            # Decide inclusion:
            include = True
            if tag_filters:
                # Match tags by exact or prefix (case-insensitive)
                lowered = [t.lower() for t in tags]
                include = any(
                    any(lt == flt or lt.startswith(flt) for flt in tag_filters)
                    for lt in lowered
                )
            # Fallback to path prefix if tags don't match or tags are missing
            if (not include) and path_filters:
                include = any(str(path).startswith(pref) for pref in path_filters)
            if not include:
                continue
            ops.append({
                "path": path,
                "method": method.lower(),
                "operationId": op.get("operationId") or f"{method}_{path}",
                "parameters": op.get("parameters", []),
                "requestBody": op.get("requestBody"),
                "responses": op.get("responses", {}),
                "tags": tags,
            })
    return ops


def response_schema_ref(op: Dict[str, Any]) -> Optional[str]:
    for status in ("200", "201", "default"):
        resp = op["responses"].get(status)
        if not resp:
            continue
        # OpenAPI v3
        if "content" in resp:
            content = resp.get("content", {})
            for mt in ["application/json", "application/json; charset=utf-8"]:
                if mt in content:
                    sch = content[mt].get("schema", {})
                    if "$ref" in sch:
                        return sch["$ref"]
                    if sch.get("type") == "array" and "$ref" in sch.get("items", {}):
                        return sch["items"]["$ref"]
        # Swagger v2
        ref = resp.get("schema", {}).get("$ref")
        if ref is not None:
            return ref
    return None


def request_schema_ref(op: Dict[str, Any]) -> Optional[str]:
    print(f"request_schema_ref {json.dumps(op, indent=4)}")
    # OpenAPI v3
    body = op.get("requestBody")
    if isinstance(body, dict):
        content = body.get("content", {})
        for mt in ["application/json", "application/json; charset=utf-8"]:
            if mt in content:
                sch = content[mt].get("schema", {})
                if "$ref" in sch:
                    return sch["$ref"]
                if sch.get("type") == "array" and "$ref" in sch.get("items", {}):
                    return sch["items"]["$ref"]
    # Swagger v2
    for p in op.get("parameters", []):
        if p.get("in") in ("body", ""):
            sch = p.get("schema", {})
            if "$ref" in sch:
                return sch["$ref"]
    return None


def extract_module_from_path(path: str) -> Optional[str]:
    # Supports styles: /mint.v5.QueryService/Params or /mint/v5/params
    p = path.strip("/")
    if "." in p.split("/")[0]:
        first = p.split("/")[0]
        # e.g., mint.v5.QueryService -> mint.v5
        parts = first.split(".")
        if len(parts) >= 2:
            return f"{parts[0]}.{parts[1]}"
    segs = p.split("/")
    if len(segs) >= 2:
        return f"{segs[0]}.{segs[1]}"
    return None


def split_ref_tail(ref: str) -> str:
    # Return the last component name without the #/definitions or #/components/schemas prefix
    if not ref:
        return ""
    if "/" in ref:
        return ref.split("/")[-1]
    return ref


def map_v2_name_to_classes(module: str, name: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Map a v2 definition name like 'mintv2GetParamsResponse' to betterproto2 module and class names.

    Returns (module_path, RespClass, ReqClass)
    """
    module_path = f"{PROTO_BASE}.{module.replace('.', '.')}"
    collapsed = module.replace(".", "")  # e.g., 'mintv5'
    # Strip prefix if present (case-insensitive)
    low = name.lower()
    pref = collapsed.lower()
    short = name[len(collapsed):] if low.startswith(pref) else name
    # short should be SomethingResponse or SomethingRequest
    resp = short if short.endswith("Response") else None
    req = short if short.endswith("Request") else None
    if not resp and short.endswith("request"):
        req = short[:-7] + "Request"
    if not req and short.endswith("response"):
        resp = short[:-8] + "Response"
    return module_path, resp, req


def param_list(params: List[Dict[str, Any]]) -> List[Tuple[str, str, str]]:
    """Return [(name, in_, py_type_str)]."""
    out: List[Tuple[str, str, str]] = []
    for p in params:
        name = p.get("name")
        loc = p.get("in")  # path, query, header
        schema = p.get("schema", {})
        typ = schema.get("type", "string")
        if typ == "integer":
            py = "int"
        elif typ == "number":
            py = "float"
        elif typ == "boolean":
            py = "bool"
        else:
            py = "str"
        if name and loc:
            out.append((name, loc, py))
    return out


def gen_client(spec: Dict[str, Any], include_tags: List[str], include_path_prefix: List[str]) -> str:
    ops = collect_operations(spec, include_tags, include_path_prefix)

    # Group operations by primary tag (first matching include_tag)
    tag_order = [t.lower() for t in include_tags]
    grouped: Dict[str, List[Dict[str, Any]]] = {t: [] for t in include_tags}
    def pick_tag(op_tags: List[str]) -> Optional[str]:
        lower = [t.lower() for t in op_tags]
        for desired in tag_order:
            for lt, ot in zip(lower, op_tags):
                if lt == desired or lt.startswith(desired):
                    return ot  # return original-cased tag
        return None

    for op in ops:
        chosen = pick_tag(op.get("tags", []))
        if not chosen:
            # fallback: derive from path
            p = op["path"].strip("/").split("/")
            if len(p) >= 2:
                candidate = f"{p[0]}.{p[1]}"
                if candidate in grouped:
                    chosen = candidate
        if not chosen:
            # If still unknown, put into first bucket
            chosen = include_tags[0] if include_tags else "default"
            if chosen not in grouped:
                grouped[chosen] = []
        grouped.setdefault(chosen, []).append(op)

    # Resolve module/classes for each op (include fallback), then collect imports
    resolved_by_tag: Dict[str, List[Dict[str, Any]]] = {k: [] for k in grouped.keys()}
    for tag, ops_list in grouped.items():
        for op in ops_list:
            print(f"op: {op}")
            proto_module, proto_class, req_class = (None, None, None)
            # Prefer OpenAPI v3 component schema names
            ref_resp = response_schema_ref(op)
            if ref_resp and "#/components/schemas/" in ref_resp:
                m = guess_proto_import_for_schema(ref_resp)
                if m:
                    proto_module, proto_class = m
            # Swagger v2: #/definitions/<name>
            if not proto_class and ref_resp and "#/definitions/" in ref_resp:
                module_tag = extract_module_from_path(op["path"]) or tag
                name = split_ref_tail(ref_resp)
                module_path, resp_guess, _ = map_v2_name_to_classes(module_tag, name)
                print(f"map_v2_name_to_classes({module_tag}, {name}) = {module_path}, {resp_guess}, _")
                proto_module = module_path
                proto_class = resp_guess

            print(f"proto: {proto_module} :: {proto_class} :: {req_class}")

            # Request class
            req_class = proto_class[:-len("Response")] + "Request" if proto_class.endswith("Response") else None
            # ref_req = request_schema_ref(op)
            # print(f"ref_req = {ref_req}")
            # if ref_req and "#/components/schemas/" in ref_req:
            #     m = guess_proto_import_for_schema(ref_req)
            #     if m:
            #         _, req_class = m
            # if not req_class and ref_req and "#/definitions/" in ref_req:
            #     module_tag = extract_module_from_path(op["path"]) or tag
            #     name = split_ref_tail(ref_req)
            #     _, _, req_guess = map_v2_name_to_classes(module_tag, name)
            #     print(f"map_v2_name_to_classes({module_tag}, {name}) = {req_guess}")
            #     req_class = req_guess

            if not proto_class:
                fb = fallback_proto_from_tag_and_path(tag, op["path"])
                if fb:
                    module, meth_resp, meth_req = fb
                    # Prefer operationId-derived method if available
                    mname = method_from_operation_id(op) or meth_resp[:-8]
                    guessed_resp, guessed_req = introspect_classes(module, mname)
                    proto_module = module
                    proto_class = guessed_resp or f"{mname}Response"
                    req_class = req_class or guessed_req or f"{mname}Request"
            resolved_by_tag[tag].append({
                "op": op,
                "module": proto_module,
                "resp_class": proto_class,
                "req_class": req_class,
            })

    imports_classes: Dict[str, List[str]] = {}
    for ops_list in resolved_by_tag.values():
        for item in ops_list:
            mod = item["module"]
            if not mod:
                continue
            for cls in (item["resp_class"], item["req_class"]):
                if cls:
                    imports_classes.setdefault(mod, []).append(cls)

    # Build imports block
    imports_code = [
        "from __future__ import annotations",
        "import aiohttp",
        "from typing import Optional, TypeVar, Type",
        "import betterproto2",
    ]
    # Build module-level imports as aliases, so we can reference alias.Class
    def module_alias(module: str, used: set[str]) -> str:
        parts = module.split('.')
        # Try last two segments for brevity
        cand = "_".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
        if cand in used:
            cand = "_".join(parts[-3:]) if len(parts) >= 3 else cand
        if cand in used:
            i = 1
            base = cand
            while f"{base}_{i}" in used:
                i += 1
            cand = f"{base}_{i}"
        used.add(cand)
        return cand

    module_aliases: Dict[str, str] = {}
    used_aliases: set[str] = set()
    for module in sorted(imports_classes.keys()):
        alias = module_alias(module, used_aliases)
        module_aliases[module] = alias
        imports_code.append(f"import {module} as {alias}")

    # Helper parse function (dict -> betterproto2) with precise typing
    helper = r'''
T = TypeVar("T", bound=betterproto2.Message)

def _parse_to_proto(cls: Type[T], data: dict) -> T:
    """Recursively instantiate a betterproto2 Message from a dict, returning the concrete type T."""
    ann = getattr(cls, "__annotations__", {})
    kwargs = {}
    for k, v in (data or {}).items():
        t = ann.get(k)
        if t is None:
            continue
        # Nested message
        try:
            import inspect
            origin = getattr(t, "__origin__", None)
            args = getattr(t, "__args__", ())
            if inspect.isclass(t) and issubclass(t, betterproto2.Message):
                kwargs[k] = _parse_to_proto(t, v) if isinstance(v, dict) else v
            elif origin in (list, tuple) and args:
                elem_t = args[0]
                if isinstance(v, list):
                    def _coerce(x):
                        if inspect.isclass(elem_t) and issubclass(elem_t, betterproto2.Message) and isinstance(x, dict):
                            return _parse_to_proto(elem_t, x)
                        return x
                    kwargs[k] = [_coerce(x) for x in v]
                else:
                    kwargs[k] = v
            else:
                # Scalars: rely on Python implicit coercion where possible
                kwargs[k] = v
        except Exception:
            kwargs[k] = v
    return cls(**kwargs)
'''

    # Build one client class per tag
    classes_code: List[str] = []
    for tag, ops_list in resolved_by_tag.items():
        if not ops_list:
            continue
        # Class name: EmissionsV9RestClient from emissions.v9
        cname = "RestClient"
        if "." in tag:
            pkg, ver = tag.split(".", 1)
            cname = f"{pkg.capitalize()}{ver.upper()}RestClient"
        else:
            cname = f"{tag.title().replace('.', '')}RestClient"

        header = f"""
class {cname}:
    def __init__(self, base_url: str, session: Optional[aiohttp.ClientSession] = None):
        self._base_url = base_url.rstrip('/')
        self._session = session or aiohttp.ClientSession()

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
"""

        functions: List[str] = []
        for item in ops_list:
            op = item["op"]
            proto_module = item["module"]
            proto_class = item["resp_class"]
            req_class = item["req_class"]

            func_name = method_name_from_path(op["path"], op.get("operationId") or f"{op['method']}_{op['path']}")
            params = param_list(op["parameters"])  # name, in, type

            # Ensure we have a module alias if needed
            alias = module_aliases.get(proto_module) if proto_module else None

            sig_parts = ["self"]
            # Determine if a request is expected (body or any path/query params)
            expects_request = bool(op.get("requestBody")) or bool(params)
            if expects_request:
                if not (req_class and alias):
                    raise RuntimeError(f"Cannot resolve request class for {op['method'].upper()} {op['path']} (tag={tag}); check OpenAPI or mapping")
                sig_parts.append(f"message: {alias}.{req_class}")
            sig = ", ".join(sig_parts)

            path_expr = op["path"]
            for name, loc, _ in params:
                if loc == "path":
                    path_expr = path_expr.replace("{" + name + "}", f"{'{'}message.{snake(name)}{'}'}")

            # Build query dict from message fields at runtime
            query_names = [name for name, loc, _ in params if loc == "query"]
            if query_names:
                query_build = "{ " + ", ".join([f"\"{n}\": getattr(message, '{n}', None) if message is not None else None" for n in query_names]) + " }"
            else:
                query_build = "None"

            return_ann = f"{alias}.{proto_class}" if (proto_class and alias) else "dict"
            body_line = "json=None"
            if op.get("requestBody"):
                # If requestBody exists and message is a dict or proto, send its dict
                body_line = "json=(message.to_dict() if hasattr(message, 'to_dict') else message)"

            if proto_class and alias:
                parse_block = (
                    "        data = await resp.json()\n"
                    f"        return _parse_to_proto({alias}.{proto_class}, data)\n"
                )
            else:
                raise RuntimeError(
                    f"Cannot resolve response class for {op['method'].upper()} {op['path']} (tag={tag}); check OpenAPI or mapping"
                )

            func = f'''
    async def {func_name}({sig}) -> {return_ann}:
        """Auto-generated from OpenAPI. Tags: {', '.join(op.get('tags', []))}"""
        url = self._base_url + f"{path_expr}"
        async with self._session.request("{op['method'].upper()}", url, params=params, {body_line}) as resp:
            resp.raise_for_status()
{parse_block}
'''
            functions.append(func)

        classes_code.append(header + "\n".join(functions))

    code = "\n".join(imports_code) + "\n\n" + helper + "\n" + "\n\n".join(classes_code)
    return code


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True, help="Path to OpenAPI JSON or YAML")
    ap.add_argument("--out", required=True, help="Output .py path for generated client")
    ap.add_argument(
        "--include-tags",
        nargs="*",
        default=["emissions.v9", "mint.v5"],
        help="Only include operations with these tags (case-insensitive, prefix allowed)",
    )
    ap.add_argument(
        "--include-path-prefix",
        nargs="*",
        default=[],
        help="Additionally include operations whose path starts with any of these prefixes (e.g., /emissions/v9 /mint/v5)",
    )
    args = ap.parse_args()

    spec = load_spec(Path(args.spec))
    prefixes = list(args.include_path_prefix)
    # Derive default prefixes from tags if none provided
    if not prefixes:
        for t in args.include_tags:
            if "." in t:
                pkg, ver = t.split(".", 1)
                prefixes.append(f"/{pkg}/{ver}")
    code = gen_client(spec, include_tags=list(args.include_tags), include_path_prefix=prefixes)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(code, encoding="utf-8")
    print(f"Wrote generated client: {out_path}")


if __name__ == "__main__":
    main()


