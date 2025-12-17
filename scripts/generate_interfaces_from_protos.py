#!/usr/bin/env python3
"""
Generate Protocol interfaces from Protobuf Files

Generates Python Protocol interfaces from protobuf files with google.api.http annotations.
These interfaces can be implemented by both REST and gRPC clients.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Any
import logging

import betterproto2
from jinja2 import Environment, FileSystemLoader

from analyze_protos import ProtobufModule, ProtobufModules, analyze_proto, MethodInfo, ServiceInfo, split_entity_name

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("allora_sdk")


class ProtobufAnalyzer:
    def __init__(self, base_import_path: str = "allora_sdk.rpc_client.protos"):
        self.base_import_path = base_import_path
        self.discovered_modules: List[ProtobufModule] = []

    def discover_modules(self, include_tags: List[str], proto_files_dirs: List[Path], include_dirs: List[Path]):
        proto_files = []
        for tag in include_tags:
            # Convert tag like "emissions.v9" to file pattern
            tag_parts = tag.split('.')
            for d in proto_files_dirs:
                potential_files = list(d.glob(f"**/{'/'.join(tag_parts)}/**/*.proto"))
                proto_files.extend(potential_files)

            if not proto_files:
                logger.warning(f"No proto files found for tag {tag}")
            files = '\n'.join([str(f) for f in proto_files])
            logger.info(f"Analyzing proto files for {tag}: \n{files}")

        return analyze_proto(proto_files, include_dirs)

class InterfaceGenerator:
    """Generates Protocol interface code from protobuf module information."""

    def __init__(self, base_import_path: str, modules: ProtobufModules):
        self.generated_exports: List[Tuple[str, str, str]] = []  # List of (filename, class_name, protocol_name) tuples
        self.base_import_path = base_import_path
        self.modules = modules

        # Initialize Jinja2 environment
        template_dir = Path(__file__).parent / "templates"
        self.jinja_env = Environment(
            loader=FileSystemLoader(template_dir),
            keep_trailing_newline=True
        )

    def generate_interfaces(self, module: ProtobufModule) -> str:
        """Generate Protocol interface code for a protobuf module."""
        filename = f"{module.name.replace('.', '_')}_interface"

        # Prepare data for template
        types_by_module = self._collect_types(module)
        services = []

        for name, service in module.services.items():
            if len(service.methods) == 0:
                logging.info(f"skipping interface generation for {name}")
                continue

            class_name = self._get_client_class_name(module.name, service.name)
            protocol_name = self._get_protocol_name(module.name, service.name)
            self.generated_exports.append((filename, class_name, protocol_name))

            service_data = self._prepare_service_data(module, service, class_name, protocol_name)
            services.append(service_data)

        # Render template
        template = self.jinja_env.get_template("interface.py.jinja")
        return template.render(
            base_import_path=self.base_import_path,
            types_by_module=types_by_module,
            services=services
        )

    def _collect_types(self, module: ProtobufModule) -> Dict[str, List[Tuple[str, str]]]:
        """Collect all types used in the module and organize them by source module."""
        request_types = set()
        response_types = set()

        for _, service in module.services.items():
            for _, method in service.methods.items():
                request_types.add(method.request_type)
                response_types.add(method.response_type)

        all_types = sorted(request_types | response_types)

        types_by_module = {}
        for t in all_types:
            mod_name, type_name = split_entity_name(t)
            if mod_name not in types_by_module:
                types_by_module[mod_name] = []

            betterproto_name = str(type_name).replace('ABCI', 'Abci')
            betterproto_name = str(betterproto_name).replace('QueryAccountAddressByID', 'QueryAccountAddressById')

            types_by_module[mod_name].append((type_name, betterproto_name))

        return types_by_module

    def _prepare_service_data(self, module: ProtobufModule, service: ServiceInfo, class_name: str, protocol_name: str) -> Dict[str, Any]:
        """Prepare service data for template rendering."""
        methods_with_bindings = [m for _, m in service.methods.items() if m.http and len(m.http) > 0]
        methods_without_bindings = [m for _, m in service.methods.items() if not m.http or len(m.http) == 0]

        if methods_without_bindings:
            logger.info(f"  Skipped {len(methods_without_bindings)} methods without HTTP bindings: {', '.join([m.name for m in methods_without_bindings[:5]])}{('...' if len(methods_without_bindings) > 5 else '')}")

        prepared_methods = [self._prepare_method_data(module, method) for method in methods_with_bindings]

        return {
            "class_name": class_name,
            "protocol_name": protocol_name,
            "title": service.name.title(),
            "methods_with_bindings": prepared_methods
        }

    def _prepare_method_data(self, module: ProtobufModule, method: MethodInfo) -> Dict[str, Any]:
        """Prepare method data for template rendering."""
        binding = method.http[0]
        path = binding.path

        # Convert path params from {param} to {message.param_snake_case}
        for param in binding.path_params:
            proto_param = f"{{{param}}}"
            field_name = _camel_to_snake(param)
            python_param = f"{{message.{field_name}}}"
            path = path.replace(proto_param, python_param)

        # Get query parameters (all request fields except path params)
        request_type = self.modules.get_message(method.request_type)
        if not request_type:
            logger.error(f"could not find request type {method.request_type}")
            return {}

        query_params = sorted(set(request_type.fields).difference(binding.path_params))

        return {
            "snake_name": _camel_to_snake(method.name),
            "request_type_name": method.request_type.replace('.', '_'),
            "response_type_name": method.response_type.replace('.', '_'),
            "has_path_params": len(binding.path_params) > 0,
            "query_params": query_params,
            "url_path": path
        }


    def _get_client_class_name(self, module_name: str, service_name: str) -> str:
        """Get the REST client class name (for compatibility)."""
        parts = module_name.split('.')
        return f"{''.join(p.title() for p in parts)}Rest{service_name}Client"

    def _get_protocol_name(self, module_name: str, service_name: str) -> str:
        """Get the protocol interface name."""
        parts = module_name.split('.')
        return f"{''.join(p.title() for p in parts)}{service_name}Like"

    def generate_init_file(self) -> str:
        """Generate __init__.py file with exports for all generated interfaces."""
        if not self.generated_exports:
            return ""

        # Render template
        template = self.jinja_env.get_template("interface__init__.py.jinja")
        return template.render(exports=self.generated_exports)

def _camel_to_snake(camel_str: str) -> str:
    """Convert camelCase to snake_case."""
    # Insert an underscore before any uppercase letter that follows a lowercase letter
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', camel_str)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate Protocol interfaces from protobuf files with HTTP annotations"
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output directory for generated files"
    )
    parser.add_argument(
        "--include-tags",
        required=True,
        nargs="+",
        help="Protobuf modules to generate for (e.g., 'emissions.v9 mint.v5')"
    )
    parser.add_argument(
        "--base-import-path",
        default="allora_sdk.rpc_client.protos",
        help="Base import path for protobuf modules"
    )
    parser.add_argument(
        "--proto-files-dirs",
        nargs="+",
        default=[],
        help="Directories containing proto files to analyze"
    )
    parser.add_argument(
        "--include-dirs",
        nargs="+",
        default=[],
        help="Include directories for proto file analysis"
    )

    args = parser.parse_args()

    # Ensure output directory exists
    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Prepare paths
    proto_files_dirs = [Path(d) for d in args.proto_files_dirs]
    include_dirs = [Path(d) for d in args.include_dirs] if args.include_dirs else proto_files_dirs

    # Discover and analyze modules
    analyzer = ProtobufAnalyzer(args.base_import_path)
    modules = analyzer.discover_modules(args.include_tags, proto_files_dirs, include_dirs)

    if len(modules) == 0:
        logger.error("No modules discovered. Check your include-tags and proto file paths.")
        sys.exit(1)
    else:
        logger.info(f"{len(modules)} modules discovered.")
        for _, m in modules.modules.items():
            logger.info(f"  - {m.name}")

    generator = InterfaceGenerator(args.base_import_path, modules)

    for name, module in modules:
        if len(module.services) == 0:
            logger.info(f"Skipping interface generation for {name}...")
            continue

        logger.info(f"Generating Protocol interfaces for {name}...")

        interface_code = generator.generate_interfaces(module)
        filename = f"{module.name.replace('.', '_')}_interface.py"
        output_file = output_dir / filename

        with open(output_file, 'w') as f:
            f.write(interface_code)

        logger.info(f"Generated {output_file}")

    init_content = generator.generate_init_file()
    if init_content:
        init_file = output_dir / "__init__.py"
        with open(init_file, 'w') as f:
            f.write(init_content)
        logger.info(f"Generated {init_file}")

    logger.info(f"Generated Protocol interfaces for {len(modules)} modules in {output_dir}")


if __name__ == "__main__":
    main()
