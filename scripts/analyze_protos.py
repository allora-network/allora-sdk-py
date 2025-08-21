#!/usr/bin/env python3
from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterable, List, Optional, Sequence, Tuple, Dict

from google.protobuf import descriptor_pb2
from google.protobuf import descriptor_pool
from google.protobuf import message_factory

from google.api import annotations_pb2, http_pb2


# ---------------------- Data model ----------------------

@dataclass(frozen=True)
class HttpBinding:
    verb: str                    # "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | "CUSTOM"
    path: str                    # "/emissions/v9/reputers_stakes/{topic_id}"
    path_params: List[str]       # params from the protobuf that are consumed by the path params (the rest go to the query params for GET requests)
    body: Optional[str]          # e.g., "*" or "resource", None if not set
    custom_kind: Optional[str]   # e.g., "REPORT" for custom; None otherwise


@dataclass(frozen=True)
class MethodInfo:
    name: str
    request_type: str
    response_type: str
    http: List[HttpBinding]


@dataclass(frozen=True)
class ServiceInfo:
    name: str
    methods: Dict[str, MethodInfo]


@dataclass(frozen=True)
class MessageInfo:
    name: str
    fields: List[str]


@dataclass(frozen=True)
class ProtobufModule:
    name: str            # e.g., "emissions.v9"
    services: Dict[str, ServiceInfo]
    messages: Dict[str, MessageInfo]


class ProtobufModules:
    """
    Helper class to make consuming the outputs of analyze_proto less verbose
    """
    def __init__(self, modules: Optional[Dict[str, ProtobufModule]] = None):
        self.modules = modules or {}

    def get_module(self, path: str):
        return self.modules.get(path)

    def get_message(self, full_path: str):
        mod_name, msg_name = split_entity_name(full_path)
        mod = self.modules.get(mod_name)
        if mod is None:
            return None
        return mod.messages.get(msg_name)

    def get_service(self, full_path: str):
        mod_name, msg_name = split_entity_name(full_path)
        mod = self.modules.get(mod_name)
        if mod is None:
            return None
        return mod.messages.get(msg_name)

    def len(self):
        return len(self.modules)

    def __iter__(self):
        for name, mod in self.modules.items():
            yield name, mod

    def __len__(self):
        return len(self.modules)

def split_entity_name(full_path: str):
    """Given a fully-qualified `full_path` like `emissions.v9.InsertWorkerPayload`, splits the module path and the type name"""
    parts = full_path.split('.')
    mod_name = '.'.join(parts[0 : len(parts)-1])
    entity_name = parts[len(parts)-1]
    return mod_name, entity_name


# ---------------------- Helpers ----------------------

def _decode_http_rule(method_options: descriptor_pb2.MethodOptions):
    """
    Extract google.api.http bindings (primary + additional_bindings) from a MethodOptions.
    Returns an empty tuple if no http option is present.
    """
    # The extension symbol is defined in annotations_pb2, not http_pb2.
    if not method_options.HasExtension(annotations_pb2.http):
        return []

    rule: http_pb2.HttpRule = method_options.Extensions[annotations_pb2.http]

    def one_binding(r: http_pb2.HttpRule) -> HttpBinding:
        verb: str
        path: str
        custom_kind: Optional[str] = None

        if r.get:
            verb, path = "GET", r.get
        elif r.put:
            verb, path = "PUT", r.put
        elif r.post:
            verb, path = "POST", r.post
        elif r.delete:
            verb, path = "DELETE", r.delete
        elif r.patch:
            verb, path = "PATCH", r.patch
        elif r.custom.kind or r.custom.path:
            verb, path = "CUSTOM", r.custom.path
            custom_kind = r.custom.kind
        else:
            verb, path = "UNKNOWN", ""

        path_params = re.findall(r'\{(\w+)\}', path)
        body = None
        return HttpBinding(verb=verb, path=path, path_params=path_params, body=body, custom_kind=custom_kind)

    bindings: List[HttpBinding] = [ one_binding(rule) ]
    for extra in rule.additional_bindings:
        bindings.append(one_binding(extra))

    return bindings

def _run_protoc_to_descriptor_set(
    proto_files: Sequence[Path],
    include_paths: Sequence[Path],
) -> descriptor_pb2.FileDescriptorSet:
    """
    Invoke protoc to generate a FileDescriptorSet that includes imports and source info.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "desc.pb"
        cmd = [
            "protoc",
            "--include_imports",
            "--include_source_info",
            f"--descriptor_set_out={out}",
        ]
        for inc in include_paths:
            cmd.append(f"-I{os.path.abspath(inc)}")
        cmd.extend(os.path.abspath(str(p)) for p in proto_files)

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                "protoc failed:\n"
                f"cmd: {' '.join(cmd)}\n"
                f"stdout:\n{proc.stdout}\n"
                f"stderr:\n{proc.stderr}"
            )

        fds = descriptor_pb2.FileDescriptorSet()
        fds.ParseFromString(out.read_bytes())
        return fds


def _pool_from_fds(fds: descriptor_pb2.FileDescriptorSet) -> descriptor_pool.DescriptorPool:
    """
    Build a DescriptorPool from a FileDescriptorSet.
    """
    pool = descriptor_pool.DescriptorPool()
    # Add files in dependency order. In practice, adding in listed order usually works
    # because protoc topologically sorts, but we can be defensive.
    name_to_fd = { fd.name: fd for fd in fds.file }
    added: set[str] = set()

    def add_file(fd: descriptor_pb2.FileDescriptorProto) -> None:
        if fd.name in added:
            return
        for dep in fd.dependency:
            if dep in name_to_fd:
                add_file(name_to_fd[dep])
        pool.Add(fd)  # type: ignore[arg-type]  # proto->C++ API accepts FileDescriptorProto
        added.add(fd.name)

    for fd in fds.file:
        add_file(fd)
    return pool


# ---------------------- Public API ----------------------

def analyze_proto(
    proto_files: Sequence[Path],
    include_paths: Sequence[Path],
):
    """
    Parse one or more .proto files (plus includes) and return the services found with methods
    and decoded google.api.http options.
    """
    fds = _run_protoc_to_descriptor_set(proto_files, include_paths)
    pool = _pool_from_fds(fds)

    modules: Dict[str, ProtobufModule] = {}

    # Walk the raw FileDescriptorProtos so we keep it simple & predictable
    for file_proto in fds.file:
        mod_services: Dict[str, ServiceInfo] = {}
        mod_messages: Dict[str, MessageInfo] = {}
        module_name = file_proto.package

        for msg in file_proto.message_type:
            mod_messages[msg.name] = MessageInfo(
                name=msg.name,
                fields=list(map(lambda f: f.name, msg.field)),
            )

        for svc in file_proto.service:
            methods: Dict[str, MethodInfo] = {}
            for m in svc.method:
                request_type = m.input_type.lstrip(".")     # ".pkg.Msg" -> "pkg.Msg"
                response_type = m.output_type.lstrip(".")
                http_bindings = _decode_http_rule(m.options)

                methods[m.name] = MethodInfo(
                    name=m.name,
                    request_type=request_type,
                    response_type=response_type,
                    http=http_bindings,
                )

            mod_services[svc.name] = ServiceInfo(name=svc.name, methods=methods)

        module = modules.get(module_name)
        if module is not None:
            module.messages.update(mod_messages)
            module.services.update(mod_services)
        else:
            modules[module_name] = ProtobufModule(
                name=module_name,
                services=mod_services,
                messages=mod_messages,
            )

    return ProtobufModules(modules)


# ---------------------- CLI for rapid eyeball-testing of this layer ----------------------

def _main(argv: Sequence[str]) -> int:
    import argparse
    from pprint import pprint

    parser = argparse.ArgumentParser(description="Introspect services/methods/http from .proto")
    parser.add_argument("proto", nargs="+", help="Path(s) to .proto file(s)")
    parser.add_argument(
        "-I", "--include", action="append", default=[],
        help="Include path(s) for imports (e.g., project root, googleapis/)"
    )
    args = parser.parse_args(argv[1:])

    protos = [Path(p).resolve() for p in args.proto]
    includes = [Path(p).resolve() for p in args.include] or [Path(".").resolve()]

    infos = analyze_proto(protos, includes)
    pprint(infos)
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    raise SystemExit(_main(sys.argv))
