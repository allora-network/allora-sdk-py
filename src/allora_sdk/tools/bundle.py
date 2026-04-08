"""
Allora Model Bundle CLI

This tool helps users create, validate, and build model bundles for deployment
on the Allora hosting platform.
"""

import argparse
import sys
import tarfile
import yaml
from pathlib import Path
from typing import Optional, Dict, Any, List


SCHEMA_VERSION = "0.1"
SUPPORTED_PYTHON_VERSIONS = ["3.10", "3.11", "3.12", "3.13"]


class BundleValidationError(Exception):
    """Raised when bundle validation fails."""
    pass


def validate_model_yaml(yaml_path: Path) -> Dict[str, Any]:
    """
    Validate model.yaml structure and return parsed content.

    Args:
        yaml_path: Path to model.yaml file

    Returns:
        Parsed yaml content

    Raises:
        BundleValidationError: If validation fails
    """
    if not yaml_path.exists():
        raise BundleValidationError(f"model.yaml not found at {yaml_path}")

    try:
        with open(yaml_path) as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise BundleValidationError(f"Failed to parse model.yaml: {e}")

    if not config:
        raise BundleValidationError("model.yaml is empty")

    # Validate required fields
    required_fields = {
        "schema_version": str,
        "name": str,
        "version": str,
        "python": dict,
        "entrypoint": dict,
        "environment": dict,
    }

    for field, field_type in required_fields.items():
        if field not in config:
            raise BundleValidationError(f"Missing required field: {field}")
        if not isinstance(config[field], field_type):
            raise BundleValidationError(f"Field '{field}' must be of type {field_type.__name__}")

    # Validate python.version
    python_config = config["python"]
    if "version" not in python_config:
        raise BundleValidationError("Missing required field: python.version")

    py_version = str(python_config["version"])
    if py_version not in SUPPORTED_PYTHON_VERSIONS:
        raise BundleValidationError(
            f"Unsupported Python version: {py_version}. "
            f"Supported versions: {', '.join(SUPPORTED_PYTHON_VERSIONS)}"
        )

    # Validate entrypoint
    entrypoint = config["entrypoint"]
    required_entrypoint_fields = ["kind", "module", "function"]
    for field in required_entrypoint_fields:
        if field not in entrypoint:
            raise BundleValidationError(f"Missing required entrypoint field: {field}")

    if entrypoint["kind"] != "python_function":
        raise BundleValidationError(
            f"Unsupported entrypoint kind: {entrypoint['kind']}. "
            f"Currently only 'python_function' is supported."
        )

    # Validate environment.pip_requirements
    environment = config["environment"]
    if "pip_requirements" not in environment:
        raise BundleValidationError("Missing required field: environment.pip_requirements")

    return config


def validate_requirements_txt(bundle_root: Path, requirements_path: str) -> List[str]:
    """
    Validate that requirements.txt exists and warn about unpinned dependencies.

    Args:
        bundle_root: Root directory of the bundle
        requirements_path: Relative path to requirements.txt

    Returns:
        List of warning messages

    Raises:
        BundleValidationError: If requirements.txt doesn't exist
    """
    req_file = bundle_root / requirements_path
    if not req_file.exists():
        raise BundleValidationError(f"requirements.txt not found at {req_file}")

    warnings = []
    with open(req_file) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Check for unpinned dependencies
            if ">=" in line or "~=" in line or "^" in line:
                warnings.append(
                    f"Line {line_num}: Unpinned dependency '{line}'. "
                    "Consider using exact versions (==) for reproducibility."
                )
            elif "==" not in line and not line.startswith("-"):
                warnings.append(
                    f"Line {line_num}: Unpinned dependency '{line}'. "
                    "Consider using exact versions (==) for reproducibility."
                )

    return warnings


def validate_bundle(bundle_root: Path) -> Dict[str, Any]:
    """
    Validate complete bundle structure.

    Args:
        bundle_root: Root directory of the bundle

    Returns:
        Parsed model.yaml configuration

    Raises:
        BundleValidationError: If validation fails
    """
    print(f"Validating bundle at: {bundle_root}")

    # Validate model.yaml
    config = validate_model_yaml(bundle_root / "model.yaml")
    print("✓ model.yaml structure is valid")

    # Validate requirements.txt
    warnings = validate_requirements_txt(bundle_root, config["environment"]["pip_requirements"])
    print("✓ requirements.txt exists")

    for warning in warnings:
        print(f"⚠️  {warning}")

    # Check if entrypoint module exists (basic check)
    entrypoint = config["entrypoint"]
    module_path = entrypoint["module"].replace(".", "/") + ".py"

    # Try common locations
    possible_paths = [
        bundle_root / module_path,
        bundle_root / "src" / module_path,
    ]

    module_found = any(p.exists() for p in possible_paths)
    if not module_found:
        print(f"⚠️  Warning: Could not find module file for '{entrypoint['module']}' at expected locations")
    else:
        print(f"✓ Entrypoint module file found")

    # Check for artifacts directory if specified
    if "artifacts" in config and "root" in config["artifacts"]:
        artifacts_dir = bundle_root / config["artifacts"]["root"]
        if not artifacts_dir.exists():
            print(f"⚠️  Warning: Artifacts directory '{config['artifacts']['root']}' does not exist")
        else:
            print(f"✓ Artifacts directory exists")

    print(f"\n✅ Bundle validation passed!")
    return config


def init_bundle(name: str, target_dir: Optional[Path] = None):
    """
    Initialize a new model bundle project structure.

    Args:
        name: Name of the model/project
        target_dir: Directory to create the bundle in (defaults to ./<name>)
    """
    if target_dir is None:
        target_dir = Path.cwd() / name

    if target_dir.exists():
        print(f"Error: Directory {target_dir} already exists")
        sys.exit(1)

    print(f"Creating new bundle: {name}")
    print(f"Location: {target_dir}")

    # Create directory structure
    target_dir.mkdir(parents=True)
    (target_dir / "src" / name.replace("-", "_")).mkdir(parents=True)
    (target_dir / "artifacts").mkdir()

    # Create model.yaml
    model_yaml = {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "version": "0.1.0",
        "python": {
            "version": "3.11",
        },
        "entrypoint": {
            "kind": "python_function",
            "module": f"src.{name.replace('-', '_')}.runner",
            "function": "run",
            "workdir": ".",
            "artifacts_dir": "artifacts",
        },
        "environment": {
            "pip_requirements": "requirements.txt",
        },
        "allora": {
            "topic_id": 69,
            "polling_interval": 120,
            "fee_tier": "STANDARD",
            "network": "testnet",
        },
    }

    with open(target_dir / "model.yaml", "w") as f:
        yaml.dump(model_yaml, f, default_flow_style=False, sort_keys=False)

    # Create requirements.txt
    requirements = [
        "# Add your dependencies here with pinned versions",
        "# Example:",
        "# numpy==1.24.3",
        "# scikit-learn==1.3.0",
        "",
    ]
    with open(target_dir / "requirements.txt", "w") as f:
        f.write("\n".join(requirements))

    # Create runner.py template
    runner_code = '''"""
Model inference runner.

This function is called by the Allora worker for each submission window.
"""

def run(nonce: int) -> float:
    """
    Generate a prediction for the given nonce (block height).

    Args:
        nonce: The block height for this prediction window

    Returns:
        A scalar prediction value (float, int, str, or Decimal)

    Example:
        You can load artifacts relative to the bundle root:

        from pathlib import Path
        import joblib

        # Artifacts are in the 'artifacts/' directory
        artifacts_dir = Path(__file__).parent.parent.parent / "artifacts"
        model = joblib.load(artifacts_dir / "model.pkl")

        # Generate prediction
        prediction = model.predict([[nonce]])[0]
        return float(prediction)
    """
    # TODO: Implement your prediction logic here
    # This is just a placeholder that returns a constant value
    return 42.0
'''

    runner_path = target_dir / "src" / name.replace("-", "_") / "runner.py"
    with open(runner_path, "w") as f:
        f.write(runner_code)

    # Create __init__.py
    init_path = target_dir / "src" / name.replace("-", "_") / "__init__.py"
    with open(init_path, "w") as f:
        f.write("")

    # Create README
    readme = f"""# {name}

This is an Allora model bundle created with the `allora-bundle` CLI.

## Structure

```
{name}/
├── model.yaml              # Bundle metadata and configuration
├── requirements.txt        # Python dependencies
├── src/
│   └── {name.replace('-', '_')}/
│       └── runner.py       # Your prediction function
└── artifacts/              # Model weights, data files, etc.
```

## Development

1. Edit `src/{name.replace('-', '_')}/runner.py` to implement your prediction logic
2. Add any model files (weights, pickles, etc.) to the `artifacts/` directory
3. Update `requirements.txt` with your dependencies (use exact versions)
4. Update `model.yaml` if needed (topic_id, network, etc.)

## Testing Locally

You can test your model function directly:

```python
from src.{name.replace('-', '_')}.runner import run

result = run(nonce=12345)
print(f"Prediction: {{result}}")
```

## Building

Validate your bundle:
```bash
allora-bundle validate
```

Build a deployable archive:
```bash
allora-bundle build
```

This creates `dist/{name}-0.1.0.tar.gz` ready for deployment.
"""

    with open(target_dir / "README.md", "w") as f:
        f.write(readme)

    print("\n✅ Bundle initialized successfully!")
    print(f"\nNext steps:")
    print(f"  1. cd {name}")
    print(f"  2. Edit src/{name.replace('-', '_')}/runner.py to implement your model")
    print(f"  3. Add dependencies to requirements.txt")
    print(f"  4. Run 'allora-bundle validate' to check your bundle")
    print(f"  5. Run 'allora-bundle build' to create a deployable archive")


def build_bundle(bundle_root: Path, output_dir: Optional[Path] = None):
    """
    Build a .tar.gz archive from a validated bundle.

    Args:
        bundle_root: Root directory of the bundle
        output_dir: Output directory for the archive (defaults to ./dist)
    """
    # Validate first
    config = validate_bundle(bundle_root)

    if output_dir is None:
        output_dir = bundle_root / "dist"

    output_dir.mkdir(exist_ok=True)

    # Build archive name from config
    archive_name = f"{config['name']}-{config['version']}.tar.gz"
    archive_path = output_dir / archive_name

    print(f"\nBuilding archive: {archive_path}")

    # Create tar.gz
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(bundle_root, arcname=Path(bundle_root).name, filter=_filter_tarfile)

    file_size = archive_path.stat().st_size
    file_size_mb = file_size / (1024 * 1024)

    print(f"✅ Bundle built successfully!")
    print(f"   Archive: {archive_path}")
    print(f"   Size: {file_size_mb:.2f} MB")
    print(f"\nNext steps:")
    print(f"  - Upload this archive to your Allora hosting platform")
    print(f"  - Configure deployment settings (wallet, topic_id, network)")


def _filter_tarfile(tarinfo):
    """Filter function to exclude unwanted files from tarball."""
    exclude_patterns = [
        ".git",
        ".gitignore",
        "__pycache__",
        "*.pyc",
        "*.pyo",
        ".pytest_cache",
        ".mypy_cache",
        ".tox",
        ".venv",
        "venv",
        ".env",
        "dist",
        ".DS_Store",
    ]

    path = Path(tarinfo.name)
    for pattern in exclude_patterns:
        if pattern in path.parts or path.name == pattern:
            return None

    return tarinfo


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Allora Model Bundle CLI - Create, validate, and build model bundles",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Init command
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize a new model bundle project",
    )
    init_parser.add_argument("name", help="Name of the model/project")
    init_parser.add_argument(
        "--dir",
        type=Path,
        help="Target directory (default: ./<name>)",
    )

    # Validate command
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate a model bundle",
    )
    validate_parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path.cwd(),
        help="Path to bundle directory (default: current directory)",
    )

    # Build command
    build_parser = subparsers.add_parser(
        "build",
        help="Build a deployable .tar.gz archive",
    )
    build_parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path.cwd(),
        help="Path to bundle directory (default: current directory)",
    )
    build_parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Output directory (default: ./dist)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        if args.command == "init":
            init_bundle(args.name, args.dir)
        elif args.command == "validate":
            validate_bundle(args.path)
        elif args.command == "build":
            build_bundle(args.path, args.output)
    except BundleValidationError as e:
        print(f"\n❌ Validation error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
