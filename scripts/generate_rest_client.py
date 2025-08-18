#!/usr/bin/env python3
"""
Cosmos-LCD REST Client Generator

Generates Python REST clients from protobuf-generated service classes.
The generated REST clients match the exact method signatures of the protobuf QueryServiceStub classes.
"""

import argparse
import ast
import importlib
import inspect
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, get_type_hints
import logging
import yaml

import betterproto2

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class SwaggerParameter:
    """Swagger parameter information."""
    name: str
    location: str  # "path", "query", or "body"
    required: bool
    param_type: str  # "string", "integer", etc.
    is_array: bool = False


@dataclass
class HttpRoute:
    """HTTP route information extracted from Swagger YAML."""
    method: str  # GET, POST, etc.
    path: str    # /emissions/v9/params
    operation_id: str
    parameters: List[SwaggerParameter]
    

@dataclass
class ServiceMethod:
    """Information about a protobuf service method."""
    name: str
    request_type: str
    response_type: str
    full_request_type: str  # With module path
    full_response_type: str  # With module path
    grpc_path: str
    http_route: Optional[HttpRoute] = None


@dataclass
class ProtobufModule:
    """Information about a protobuf module."""
    name: str            # e.g., "emissions.v9"
    import_path: str     # e.g., "allora_sdk.protobuf_client.protos.emissions.v9"
    methods: List[ServiceMethod]


class SwaggerParser:
    """Parses Swagger YAML to extract HTTP route information."""
    
    def __init__(self, swagger_path: str):
        with open(swagger_path, 'r') as f:
            self.swagger_data = yaml.safe_load(f)
        self.routes_by_operation_id: Dict[str, HttpRoute] = {}
        self._parse_routes()
    
    def _parse_routes(self):
        """Parse all routes from swagger YAML."""
        paths = self.swagger_data.get('paths', {})
        
        for path, methods in paths.items():
            for method, operation in methods.items():
                if method.upper() not in ['GET', 'POST', 'PUT', 'DELETE']:
                    continue
                    
                operation_id = operation.get('operationId')
                if not operation_id:
                    continue
                
                # Parse parameters
                parameters = []
                for param in operation.get('parameters', []):
                    swagger_param = SwaggerParameter(
                        name=param['name'],
                        location=param['in'],
                        required=param.get('required', False),
                        param_type=param.get('type', 'string'),
                        is_array=param.get('type') == 'array'
                    )
                    parameters.append(swagger_param)
                
                route = HttpRoute(
                    method=method.upper(),
                    path=path,
                    operation_id=operation_id,
                    parameters=parameters
                )
                self.routes_by_operation_id[operation_id] = route
                
        logger.info(f"Parsed {len(self.routes_by_operation_id)} routes from Swagger")
    
    def get_route_for_method(self, method_name: str, module_name: str, service_class_name: str = "QueryService") -> Optional[HttpRoute]:
        """Get HTTP route for a protobuf method."""
        # Convert method name to operation ID pattern
        # e.g., get_multi_reputer_stake_in_topic -> QueryService_GetMultiReputerStakeInTopic
        # or simulate -> Service_Simulate (for cosmos.tx.v1beta1)
        pascal_method = self._to_pascal_case(method_name)
        
        # Extract service name from class name (remove 'Stub' suffix)
        service_name = service_class_name.replace('Stub', '') if service_class_name.endswith('Stub') else service_class_name
        operation_id = f"{service_name}_{pascal_method}"
        
        return self.routes_by_operation_id.get(operation_id)
    
    def _to_pascal_case(self, snake_str: str) -> str:
        """Convert snake_case to PascalCase."""
        return ''.join(word.capitalize() for word in snake_str.split('_'))


class ProtobufAnalyzer:
    """Analyzes protobuf modules to extract service information."""
    
    def __init__(self, base_import_path: str = "allora_sdk.protobuf_client.protos", swagger_parser: Optional[SwaggerParser] = None):
        self.base_import_path = base_import_path
        self.swagger_parser = swagger_parser
        self.discovered_modules: List[ProtobufModule] = []
        
    def discover_modules(self, include_tags: List[str]) -> List[ProtobufModule]:
        """Discover protobuf modules matching the include tags."""
        modules = []
        
        for tag in include_tags:
            try:
                import_path = f"{self.base_import_path}.{tag.replace('.', '.')}"
                module = importlib.import_module(import_path)
                
                # Look for *ServiceStub class
                stub_class = None
                all_stubs = []
                for attr_name in dir(module):
                    if attr_name.endswith('ServiceStub'):
                        all_stubs.append(attr_name)
                        if 'Query' in attr_name:
                            stub_class = getattr(module, attr_name)
                        # Fallback to any ServiceStub if no QueryServiceStub found
                        elif stub_class is None:
                            stub_class = getattr(module, attr_name)
                
                
                if stub_class:
                    # Find the actual stub class name for service prefix determination  
                    stub_class_name = stub_class.__name__
                    methods = self._analyze_service_class(stub_class, tag, import_path, stub_class_name)
                    
                    pb_module = ProtobufModule(
                        name=tag,
                        import_path=import_path,
                        methods=methods
                    )
                    modules.append(pb_module)
                    logger.info(f"Discovered module {tag} with {len(methods)} methods")
                else:
                    logger.warning(f"Module {tag} has no QueryServiceStub class")
                    
            except ImportError as e:
                logger.error(f"Failed to import module {tag}: {e}")
                continue
                
        return modules
    
    def _analyze_service_class(self, stub_class: Any, module_name: str, import_path: str, stub_class_name: str) -> List[ServiceMethod]:
        """Analyze a ServiceStub class to extract method information."""
        methods = []

        # Get all methods that don't start with underscore
        for method_name in dir(stub_class):
            if method_name.startswith('_'):
                continue
                
            method_obj = getattr(stub_class, method_name)
            if not callable(method_obj):
                continue
                
            # Get method signature
            try:
                sig = inspect.signature(method_obj)
                params = list(sig.parameters.keys())
                
                # Skip if not a query method (should have 'message' parameter)
                if 'message' not in params:
                    continue
                    
                # Extract type annotations
                type_hints = get_type_hints(method_obj)
                
                # Find message parameter type and return type
                message_param = sig.parameters.get('message')
                if not message_param or not message_param.annotation:
                    continue
                    
                request_type = self._clean_type_annotation(message_param.annotation)
                response_type = self._clean_type_annotation(sig.return_annotation)
                
                if not request_type or not response_type:
                    continue
                
                # Generate gRPC path (this is a convention)
                # Extract service name from stub class name (remove 'Stub' suffix)
                service_name = stub_class_name.replace('Stub', '') if stub_class_name.endswith('Stub') else stub_class_name
                grpc_path = f"/{module_name}.{service_name}/{self._to_pascal_case(method_name)}"
                
                # Try to get HTTP route from Swagger if available
                http_route = None
                if self.swagger_parser:
                    http_route = self.swagger_parser.get_route_for_method(method_name, module_name, stub_class_name)

                service_method = ServiceMethod(
                    name=method_name,
                    request_type=request_type,
                    response_type=response_type,
                    full_request_type=f"{import_path}.{request_type}",
                    full_response_type=f"{import_path}.{response_type}",
                    grpc_path=grpc_path,
                    http_route=http_route
                )
                methods.append(service_method)
                
            except Exception as e:
                logger.warning(f"Failed to analyze method {method_name}: {e}")
                continue
                
        return methods
    
    def _clean_type_annotation(self, annotation: Any) -> Optional[str]:
        """Clean up type annotation to get just the class name."""
        if annotation is None:
            return None
            
        if hasattr(annotation, '__name__'):
            return annotation.__name__
            
        # Handle string annotations
        if isinstance(annotation, str):
            # Remove quotes and extract just the class name
            cleaned = annotation.strip('"\'')
            # Handle union types like "GetParamsRequest | None"
            if '|' in cleaned:
                parts = [p.strip() for p in cleaned.split('|')]
                # Take the first non-None part
                for part in parts:
                    if part != 'None':
                        cleaned = part
                        break
            if '.' in cleaned:
                cleaned = cleaned.split('.')[-1]
            return cleaned
            
        # Handle typing constructs
        annotation_str = str(annotation)
        if '|' in annotation_str:
            # Handle Union types
            parts = [p.strip() for p in annotation_str.split('|')]
            for part in parts:
                if 'None' not in part:
                    annotation_str = part
                    break
        if '.' in annotation_str:
            return annotation_str.split('.')[-1].rstrip('>')
            
        return annotation_str
    
    def _to_pascal_case(self, snake_str: str) -> str:
        """Convert snake_case to PascalCase."""
        return ''.join(word.capitalize() for word in snake_str.split('_'))
    


class RestClientGenerator:
    """Generates REST client code from protobuf module information."""
    
    def __init__(self):
        self.generated_exports = []  # List of (filename, class_name, protocol_name) tuples
        
    def generate_client(self, module: ProtobufModule) -> str:
        """Generate REST client code for a protobuf module."""
        class_name = self._get_client_class_name(module.name)
        protocol_name = self._get_protocol_name(module.name)
        filename = f"{module.name.replace('.', '_')}_rest_client"
        
        # Track generated exports for __init__.py
        self.generated_exports.append((filename, class_name, protocol_name))
        
        # Generate imports
        imports = self._generate_imports(module)
        
        # Generate protocol interface
        protocol = self._generate_protocol(module, protocol_name)
        
        # Generate REST client class
        rest_client = self._generate_rest_client_class(module, class_name, protocol_name)
        
        return f"{imports}\n\n{protocol}\n\n{rest_client}"
    
    def _generate_imports(self, module: ProtobufModule) -> str:
        """Generate import statements for the module."""
        imports = [
            "from typing import Protocol, runtime_checkable",
            "import requests",
            "import json",
        ]
        
        # Import all request/response types
        request_types = set()
        response_types = set()
        
        for method in module.methods:
            request_types.add(method.request_type)
            response_types.add(method.response_type)
            
        all_types = sorted(request_types | response_types)
        
        if all_types:
            types_import = f"from {module.import_path} import (\n"
            for type_name in all_types:
                types_import += f"    {type_name},\n"
            types_import += ")"
            imports.append(types_import)
            
        return "\n".join(imports)
    
    def _generate_protocol(self, module: ProtobufModule, protocol_name: str) -> str:
        """Generate the protocol interface."""
        lines = [
            "@runtime_checkable",
            f"class {protocol_name}(Protocol):",
        ]
        
        # Only include methods with Swagger routes
        methods_with_routes = [m for m in module.methods if m.http_route is not None]
        if not methods_with_routes:
            # Add pass statement for empty protocols
            lines.append("    pass")
        else:
            for method in methods_with_routes:
                if method.http_route and len(method.http_route.parameters) == 0:
                    method_sig = f"    def {method.name}(self, message: {method.request_type} | None = None) -> {method.response_type}: ..."
                else:
                    method_sig = f"    def {method.name}(self, message: {method.request_type}) -> {method.response_type}: ..."
                lines.append(method_sig)
            
        return "\n".join(lines)
    
    def _generate_rest_client_class(self, module: ProtobufModule, class_name: str, protocol_name: str) -> str:
        """Generate the REST client class."""
        
        lines = [
            f"class {class_name}({protocol_name}):",
            f'    """{module.name.title()} REST client."""',
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
        
        # Generate methods (only for methods with Swagger routes)
        methods_with_routes = [m for m in module.methods if m.http_route is not None]
        for method in methods_with_routes:
            method_lines = self._generate_method(method)
            lines.extend(method_lines)
            lines.append("")
        
        # Log methods without routes
        methods_without_routes = [m for m in module.methods if m.http_route is None]
        if methods_without_routes:
            logger.info(f"  Skipped {len(methods_without_routes)} methods without Swagger routes: {', '.join([m.name for m in methods_without_routes[:5]])}{('...' if len(methods_without_routes) > 5 else '')}")
            
        return "\n".join(lines)
    
    def _generate_method(self, method: ServiceMethod) -> List[str]:
        """Generate a single REST client method."""

        if not method.http_route:
            raise Exception(f"no swagger route found for {method.name}")

            # # No swagger information - generate a placeholder
            # lines.extend([
            #     f"        # TODO: No Swagger route found for {method.name}",
            #     f"        # Implement the correct REST endpoint",
            #     f"        raise NotImplementedError(f'REST endpoint for {method.name} not implemented')"
            # ])
            # return lines

        route = method.http_route
        path = route.path
        path_params = [p for p in route.parameters if p.location == "path"]
        query_params = [p for p in route.parameters if p.location == "query"]

        if len(route.parameters) == 0:
            lines = [
                f"    def {method.name}(self, message: {method.request_type} | None = None) -> {method.response_type}:",
            ]
        else:
            lines = [
                f"    def {method.name}(self, message: {method.request_type}) -> {method.response_type}:",
            ]
        
        # Convert swagger path parameters to Python f-string format
        # e.g., {topicId} -> {message.topic_id}
        for param in path_params:
            swagger_param = f"{{{param.name}}}"
            # Convert camelCase to snake_case for message field access
            field_name = self._camel_to_snake(param.name)
            python_param = f"{{message.{field_name}}}"
            path = path.replace(swagger_param, python_param)
        
        # Build the full URL
        lines.append(f'        url = self.base_url + f"{path}"')
        
        # Handle query parameters
        if query_params:
            lines.append("        params = {}")
            for param in query_params:
                field_name = self._camel_to_snake(param.name)
                if param.is_array:
                    lines.extend([
                        f"        if hasattr(message, '{field_name}') and message.{field_name}:",
                        f"            params['{param.name}'] = message.{field_name}"
                    ])
                else:
                    lines.extend([
                        f"        if hasattr(message, '{field_name}') and message.{field_name} is not None:",
                        f"            params['{param.name}'] = message.{field_name}"
                    ])
            
            lines.extend([
                "        response = self.session.get(url, params=params)",
                "        response.raise_for_status()",
                f"        return {method.response_type}().from_json(response.text)"
            ])
        else:
            lines.extend([
                "        response = self.session.get(url)",
                "        response.raise_for_status()",
                f"        return {method.response_type}().from_json(response.text)"
            ])
        
        return lines
    
    def _camel_to_snake(self, camel_str: str) -> str:
        """Convert camelCase to snake_case."""
        # Insert an underscore before any uppercase letter that follows a lowercase letter
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', camel_str)
        return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()
    
    def _get_client_class_name(self, module_name: str) -> str:
        """Get the REST client class name."""
        parts = module_name.split('.')
        return f"{''.join(p.title() for p in parts)}RestQueryClient"
    
    def _get_protocol_name(self, module_name: str) -> str:
        """Get the protocol interface name."""
        parts = module_name.split('.')
        return f"{''.join(p.title() for p in parts)}QueryLike"
    
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


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate Cosmos-LCD REST clients from protobuf service classes"
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
        "--swagger-yaml",
        help="Path to Swagger 2.0 YAML file for HTTP route information"
    )
    
    args = parser.parse_args()
    
    # Ensure output directory exists
    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Parse Swagger YAML if provided
    swagger_parser = None
    if args.swagger_yaml:
        swagger_parser = SwaggerParser(args.swagger_yaml)
    
    # Discover and analyze modules
    analyzer = ProtobufAnalyzer(args.base_import_path, swagger_parser)
    modules = analyzer.discover_modules(args.include_tags)
    
    if not modules:
        logger.error("No modules discovered. Check your include-tags and import paths.")
        sys.exit(1)
        
    # Generate REST clients
    generator = RestClientGenerator()
    
    for module in modules:
        logger.info(f"Generating REST client for {module.name}...")
        
        # Generate code
        client_code = generator.generate_client(module)
        
        # Write to file
        filename = f"{module.name.replace('.', '_')}_rest_client.py"
        output_file = output_dir / filename
        
        with open(output_file, 'w') as f:
            f.write(client_code)
            
        logger.info(f"Generated {output_file}")
    
    # Generate __init__.py file with exports
    init_content = generator.generate_init_file()
    if init_content:
        init_file = output_dir / "__init__.py"
        with open(init_file, 'w') as f:
            f.write(init_content)
        logger.info(f"Generated {init_file}")
    
    logger.info(f"Generated REST clients for {len(modules)} modules in {output_dir}")


if __name__ == "__main__":
    main()