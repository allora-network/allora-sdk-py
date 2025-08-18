#!/usr/bin/env python3
"""
Cosmos-LCD REST Client Generator from Protobuf Files

Generates Python REST clients from protobuf files with google.api.http annotations.
The generated REST clients match the exact method signatures of the protobuf QueryServiceStub classes.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import List, Tuple
import logging

import betterproto2

from analyze_protos import ProtobufModule, ProtobufModules, analyze_proto, MethodInfo, ServiceInfo, split_entity_name

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ProtobufAnalyzer:
    def __init__(self, base_import_path: str = "allora_sdk.protobuf_client.protos"):
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
    
class RestClientGenerator:
    """Generates REST client code from protobuf module information."""
    
    def __init__(self, base_import_path: str, modules: ProtobufModules):
        self.generated_exports: List[Tuple[str, str, str]] = []  # List of (filename, class_name, protocol_name) tuples
        self.base_import_path = base_import_path
        self.modules = modules
        
    def generate_clients(self, module: ProtobufModule) -> str:
        """Generate REST client code for a protobuf module."""
        filename = f"{module.name.replace('.', '_')}_rest_client"
        
        imports = self._generate_imports(module)
        
        lines = [ imports ]
        for name, service in module.services.items():
            if len(service.methods) == 0:
                logging.info(f"skipping REST client generation for {name}")
                continue

            class_name = self._get_client_class_name(module.name, service.name)
            protocol_name = self._get_protocol_name(module.name, service.name)
            self.generated_exports.append((filename, class_name, protocol_name))

            protocol = self._generate_protocol(service, protocol_name)
            rest_client = self._generate_rest_client_class(module, service, class_name, protocol_name)
            lines.append(protocol)
            lines.append(rest_client)

        return "\n\n".join(lines)
    
    def _generate_imports(self, module: ProtobufModule) -> str:
        """Generate import statements for the module."""
        imports = [
            "from typing import Protocol, runtime_checkable",
            "import requests",
            "import json",
        ]
        
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
            if types_by_module.get(mod_name) is None:
                types_by_module[mod_name] = []
            types_by_module[mod_name].append(type_name)

        for mod, types in types_by_module.items():
            types_import = f"from {self.base_import_path}.{mod} import (\n"
            for type_name in types:
                betterproto2_type_name = str(type_name).replace('ABCI', 'Abci') # @@TODO: this is stupid
                betterproto2_type_name = str(betterproto2_type_name).replace('QueryAccountAddressByID', 'QueryAccountAddressById') # @@TODO: this is stupid
                types_import += f"    {betterproto2_type_name} as {mod.replace('.', '_')}_{type_name},\n"
            types_import += ")"
            imports.append(types_import)
            
        return "\n".join(imports)
    
    def _generate_protocol(self, service: ServiceInfo, protocol_name: str) -> str:
        lines = [
            "@runtime_checkable",
            f"class {protocol_name}(Protocol):",
        ]
        
        # Only include methods with HTTP bindings
        methods_with_bindings = [ m for _, m in service.methods.items() if m.http and len(m.http) > 0 ]
        if not methods_with_bindings:
            lines.append("    pass")
        else:
            for method in methods_with_bindings:
                # we only support the first http binding
                if len(method.http[0].path_params) == 0:
                    method_sig = f"    def {_camel_to_snake(method.name)}(self, message: {method.request_type.replace('.', '_')} | None = None) -> {method.response_type.replace('.', '_')}: ..."
                else:
                    method_sig = f"    def {_camel_to_snake(method.name)}(self, message: {method.request_type.replace('.', '_')}) -> {method.response_type.replace('.', '_')}: ..."
                lines.append(method_sig)
            
        return "\n".join(lines)
    
    def _generate_rest_client_class(self, module: ProtobufModule, service: ServiceInfo, class_name: str, protocol_name: str) -> str:
        """Generate the REST client class."""
        
        lines = [
            f"class {class_name}({protocol_name}):",
            f'    """{service.name.title()} REST client."""',
            "",
            "    def __init__(self, base_url: str):",
            '        """',
            "        Initialize REST client.",
            "",
            "        :param base_url: Base URL for the REST API",
            '        """',
            "        self.base_url = base_url.rstrip('/')",
            "        self.session = requests.Session()",
            "",
            "    def __del__(self):",
            '        """Clean up session on deletion."""',
            "        if hasattr(self, 'session'):",
            "            self.session.close()",
            "",
        ]
        
        methods_with_bindings = [ m for _, m in service.methods.items() if m.http and len(m.http) > 0 ]
        for method in methods_with_bindings:
            method_lines = self._generate_method(module, method)
            lines.extend(method_lines)
            lines.append("")
        
        methods_without_bindings = [ m for _, m in service.methods.items() if not m.http or len(m.http) == 0 ]
        if methods_without_bindings:
            logger.info(f"  Skipped {len(methods_without_bindings)} methods without HTTP bindings: {', '.join([m.name for m in methods_without_bindings[:5]])}{('...' if len(methods_without_bindings) > 5 else '')}")
            
        return "\n".join(lines)
    
    def _generate_method(self, module: ProtobufModule, method: MethodInfo) -> List[str]:
        binding = method.http[0]
        path = binding.path
        
        if len(binding.path_params) == 0:
            lines = [
                f"    def {_camel_to_snake(method.name)}(self, message: {method.request_type.replace('.', '_')} | None = None) -> {method.response_type.replace('.', '_')}:",
            ]
        else:
            lines = [
                f"    def {_camel_to_snake(method.name)}(self, message: {method.request_type.replace('.', '_')}) -> {method.response_type.replace('.', '_')}:",
            ]
        
        for param in binding.path_params:
            proto_param = f"{{{param}}}"
            field_name = _camel_to_snake(param)
            python_param = f"{{message.{field_name}}}"
            path = path.replace(proto_param, python_param)
        
        request_type = self.modules.get_message(method.request_type)
        if not request_type:
            logger.error(f"could not find request type {method.request_type}")
            return []

        query_params = set(request_type.fields).difference(binding.path_params)
        if len(query_params) == 0:
            lines.append("        params = {}")
        elif len(query_params) > 0:
            lines.append("        params = {")
            for param in query_params:
                lines.append(f'            "{param}": message.{param} if message else None,')
            lines.append("        }")

        lines.append(f'        url = self.base_url + f"{path}"')
        
        # assume all are GET requests
        lines.extend([
            "        response = self.session.get(url, params=params)",
            "        response.raise_for_status()",
            f"        return {method.response_type.replace('.', '_')}().from_json(response.text)"
        ])
        
        return lines

    def _get_client_class_name(self, module_name: str, service_name: str) -> str:
        """Get the REST client class name."""
        parts = module_name.split('.')
        return f"{''.join(p.title() for p in parts)}Rest{service_name}Client"
    
    def _get_protocol_name(self, module_name: str, service_name: str) -> str:
        """Get the protocol interface name."""
        parts = module_name.split('.')
        return f"{''.join(p.title() for p in parts)}{service_name}Like"
    
    def generate_init_file(self) -> str:
        """Generate __init__.py file with exports for all generated clients."""
        if not self.generated_exports:
            return ""
        
        # Generate import statements
        import_lines = []
        all_exports = []
        
        for filename, class_name, protocol_name in self.generated_exports:
            import_lines.append(f"from .{filename} import {class_name}, {protocol_name}")
            all_exports.extend([class_name, protocol_name])
        
        # Generate __all__ list
        all_list = "[\n"
        for export in all_exports:
            all_list += f'    "{export}",\n'
        all_list += "]"
        
        # Combine into final file content
        content = "\n".join(import_lines)
        content += "\n\n__all__ = " + all_list + "\n"
        
        return content

def _camel_to_snake(camel_str: str) -> str:
    """Convert camelCase to snake_case."""
    # Insert an underscore before any uppercase letter that follows a lowercase letter
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', camel_str)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate Cosmos-LCD REST clients from protobuf files with HTTP annotations"
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
        default="allora_sdk.protobuf_client.protos",
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
        
    generator = RestClientGenerator(args.base_import_path, modules)
    
    for name, module in modules:
        if len(module.services) == 0:
            logger.info(f"Skipping REST client generation for {name}...")
            continue

        logger.info(f"Generating REST client for {name}...")
        
        client_code = generator.generate_clients(module)
        filename = f"{module.name.replace('.', '_')}_rest_client.py"
        output_file = output_dir / filename
        
        with open(output_file, 'w') as f:
            f.write(client_code)
            
        logger.info(f"Generated {output_file}")
    
    init_content = generator.generate_init_file()
    if init_content:
        init_file = output_dir / "__init__.py"
        with open(init_file, 'w') as f:
            f.write(init_content)
        logger.info(f"Generated {init_file}")
    
    logger.info(f"Generated REST clients for {len(modules)} modules in {output_dir}")


if __name__ == "__main__":
    main()