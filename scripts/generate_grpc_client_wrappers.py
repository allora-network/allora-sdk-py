#!/usr/bin/env python3
"""
Generate gRPC client wrappers that conform to REST Protocol interfaces.

This script analyzes protobuf definitions and generates wrapper classes around
the gRPC stubs that:
1. Implement the Protocol interfaces from the REST clients
2. Handle the height parameter by converting it to gRPC metadata
"""

import argparse
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Any
import logging

from jinja2 import Environment, FileSystemLoader

from analyze_protos import ProtobufModule, ProtobufModules, analyze_proto, MethodInfo, ServiceInfo

logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
logger = logging.getLogger('allora_sdk')


def _camel_to_snake(name: str) -> str:
    """Convert CamelCase to snake_case."""
    import re
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


class GrpcWrapperGenerator:
    """Generates gRPC wrapper classes that conform to Protocol interfaces."""

    def __init__(self, base_import_path: str, interface_import_path: str, modules: ProtobufModules):
        self.generated_exports: List[Tuple[str, str]] = []  # List of (filename, class_name) tuples
        self.base_import_path = base_import_path
        self.interface_import_path = interface_import_path
        self.modules = modules

        # Initialize Jinja2 environment
        template_dir = Path(__file__).parent / "templates"
        self.jinja_env = Environment(
            loader=FileSystemLoader(template_dir),
            keep_trailing_newline=True
        )

    def generate_wrappers(self, module: ProtobufModule) -> str:
        """Generate gRPC wrapper code for a protobuf module."""
        filename = f"{module.name.replace('.', '_')}_grpc_wrapper"

        # Prepare data for template
        types_by_module = self._collect_types(module)
        proto_imports = []
        interface_imports = []
        services = []

        for name, service in module.services.items():
            if len(service.methods) == 0:
                logger.info(f"skipping gRPC wrapper generation for {name}")
                continue

            # Only generate wrappers for services that have Protocol interfaces
            # (i.e., services with HTTP bindings)
            methods_with_bindings = [m for _, m in service.methods.items() if m.http and len(m.http) > 0]
            if not methods_with_bindings:
                logger.info(f"skipping gRPC wrapper for {name} (no HTTP bindings)")
                continue

            stub_class_name = f"{service.name}Stub"
            wrapper_class_name = f"{module.name.replace('.', '_').title().replace('_', '')}{service.name}GrpcWrapper"
            protocol_name = f"{module.name.replace('.', '_').title().replace('_', '')}{service.name}Like"

            self.generated_exports.append((filename, wrapper_class_name))

            # Add imports
            proto_imports.append((module.name, stub_class_name))
            interface_module_name = f"{module.name.replace('.', '_')}_interface"
            interface_imports.append((interface_module_name, protocol_name))

            service_data = self._prepare_service_data(module, service, wrapper_class_name, protocol_name, stub_class_name)
            services.append(service_data)

        if not services:
            return ""

        # Render template
        template = self.jinja_env.get_template("grpc_client.py.jinja")
        return template.render(
            base_import_path=self.base_import_path,
            interface_import_path=self.interface_import_path,
            types_by_module=types_by_module,
            proto_imports=proto_imports,
            interface_imports=interface_imports,
            services=services
        )

    def _collect_types(self, module: ProtobufModule) -> Dict[str, List[Tuple[str, str]]]:
        """Collect all types used in the module and organize them by source module."""
        from analyze_protos import split_entity_name

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

    def _prepare_service_data(
        self,
        module: ProtobufModule,
        service: ServiceInfo,
        wrapper_class_name: str,
        protocol_name: str,
        stub_class_name: str
    ) -> Dict[str, Any]:
        """Prepare service data for template rendering."""
        methods_with_bindings = [m for _, m in service.methods.items() if m.http and len(m.http) > 0]
        prepared_methods = [self._prepare_method_data(module, method) for method in methods_with_bindings]

        return {
            "wrapper_class_name": wrapper_class_name,
            "protocol_name": protocol_name,
            "stub_class_name": stub_class_name,
            "service_name": service.name,
            "methods": prepared_methods
        }

    def _prepare_method_data(self, module: ProtobufModule, method: MethodInfo) -> Dict[str, Any]:
        """Prepare method data for template rendering."""
        binding = method.http[0]

        return {
            "name": method.name,
            "snake_name": _camel_to_snake(method.name),
            "request_type_name": method.request_type.replace('.', '_'),
            "response_type_name": method.response_type.replace('.', '_'),
            "has_path_params": len(binding.path_params) > 0,
        }

    def generate_init_file(self) -> str:
        """Generate __init__.py file with exports for all generated wrappers."""
        if not self.generated_exports:
            return ""

        # Render template
        template = self.jinja_env.get_template("grpc__init__.py.jinja")
        return template.render(exports=self.generated_exports)


class ProtobufAnalyzer:
    def __init__(self, base_import_path: str = "allora_sdk.rpc_client.protos"):
        self.base_import_path = base_import_path

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


def main():
    parser = argparse.ArgumentParser(description='Generate gRPC client wrappers')
    parser.add_argument('--out', required=True, help='Output directory for generated wrappers')
    parser.add_argument(
        "--include-tags",
        nargs="+",
        required=True,
        help="Protobuf modules to generate for (e.g., 'emissions.v9 mint.v5')"
    )
    parser.add_argument(
        "--base-import-path",
        default="allora_sdk.rpc_client.protos",
        help="Base import path for protobuf modules"
    )
    parser.add_argument(
        "--interface-import-path",
        default="allora_sdk.rpc_client.interfaces",
        help="Import path for Protocol interface modules"
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

    # Generate wrappers for each module
    generator = GrpcWrapperGenerator(args.base_import_path, args.interface_import_path, modules)
    generated_count = 0

    for module_name, module in modules:
        if len(module.services) == 0:
            logger.info(f"Skipping gRPC wrapper generation for {module_name}...")
            continue

        logger.info(f"Generating gRPC wrappers for {module_name}...")
        wrapper_code = generator.generate_wrappers(module)

        if wrapper_code:
            output_file = output_dir / f"{module_name.replace('.', '_')}_grpc_wrapper.py"
            output_file.write_text(wrapper_code)
            logger.info(f"Generated {output_file}")
            generated_count += 1
        else:
            logger.info(f"Skipping gRPC wrapper generation for {module_name} (no services)")

    # Generate __init__.py
    init_code = generator.generate_init_file()
    if init_code:
        init_file = output_dir / "__init__.py"
        init_file.write_text(init_code)
        logger.info(f"Generated {init_file}")

    logger.info(f"Generated gRPC wrappers for {generated_count} modules in {output_dir}")


if __name__ == '__main__':
    main()
