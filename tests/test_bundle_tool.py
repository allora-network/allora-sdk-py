"""
Tests for the allora-bundle CLI tool.

Tests cover validation, initialization, and building of model bundles.
"""
import pytest
import tarfile
import yaml
from pathlib import Path
from typing import Dict, Any

from allora_sdk.tools.bundle import (
    BundleValidationError,
    validate_model_yaml,
    validate_requirements_txt,
    validate_bundle,
    init_bundle,
    build_bundle,
    _filter_tarfile,
    SCHEMA_VERSION,
    SUPPORTED_PYTHON_VERSIONS,
)


@pytest.fixture
def valid_model_yaml() -> Dict[str, Any]:
    """Return a valid model.yaml configuration."""
    return {
        "schema_version": SCHEMA_VERSION,
        "name": "test-model",
        "version": "1.0.0",
        "python": {
            "version": "3.11",
        },
        "entrypoint": {
            "kind": "python_function",
            "module": "src.test_model.runner",
            "function": "run",
        },
        "environment": {
            "pip_requirements": "requirements.txt",
        },
    }


@pytest.fixture
def tmp_bundle_dir(tmp_path: Path) -> Path:
    """Create a temporary bundle directory structure."""
    bundle_dir = tmp_path / "test-bundle"
    bundle_dir.mkdir()

    # Create src directory structure
    src_dir = bundle_dir / "src" / "test_bundle"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("")
    (src_dir / "runner.py").write_text("def run(nonce: int) -> float:\n    return 42.0\n")

    # Create artifacts directory
    (bundle_dir / "artifacts").mkdir()

    return bundle_dir


def test_validate_model_yaml_success(tmp_path: Path, valid_model_yaml: Dict[str, Any]):
    """Test validation of a valid model.yaml file."""
    yaml_path = tmp_path / "model.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(valid_model_yaml, f)

    config = validate_model_yaml(yaml_path)

    assert config["name"] == "test-model"
    assert config["version"] == "1.0.0"
    assert config["python"]["version"] == "3.11"
    assert config["entrypoint"]["kind"] == "python_function"


def test_validate_model_yaml_missing_file(tmp_path: Path):
    """Test validation fails when model.yaml doesn't exist."""
    yaml_path = tmp_path / "nonexistent.yaml"

    with pytest.raises(BundleValidationError, match="model.yaml not found"):
        validate_model_yaml(yaml_path)


def test_validate_model_yaml_invalid_yaml(tmp_path: Path):
    """Test validation fails on malformed YAML."""
    yaml_path = tmp_path / "model.yaml"
    yaml_path.write_text("invalid: yaml: content: [[[")

    with pytest.raises(BundleValidationError, match="Failed to parse model.yaml"):
        validate_model_yaml(yaml_path)


def test_validate_model_yaml_empty(tmp_path: Path):
    """Test validation fails on empty YAML file."""
    yaml_path = tmp_path / "model.yaml"
    yaml_path.write_text("")

    with pytest.raises(BundleValidationError, match="model.yaml is empty"):
        validate_model_yaml(yaml_path)


def test_validate_model_yaml_missing_required_field(tmp_path: Path, valid_model_yaml: Dict[str, Any]):
    """Test validation fails when required fields are missing."""
    yaml_path = tmp_path / "model.yaml"

    # Remove required field
    del valid_model_yaml["name"]
    with open(yaml_path, "w") as f:
        yaml.dump(valid_model_yaml, f)

    with pytest.raises(BundleValidationError, match="Missing required field: name"):
        validate_model_yaml(yaml_path)


def test_validate_model_yaml_wrong_field_type(tmp_path: Path, valid_model_yaml: Dict[str, Any]):
    """Test validation fails when field has wrong type."""
    yaml_path = tmp_path / "model.yaml"

    # Wrong type for python field
    valid_model_yaml["python"] = "3.11"  # Should be dict
    with open(yaml_path, "w") as f:
        yaml.dump(valid_model_yaml, f)

    with pytest.raises(BundleValidationError, match="Field 'python' must be of type dict"):
        validate_model_yaml(yaml_path)


def test_validate_model_yaml_unsupported_python_version(tmp_path: Path, valid_model_yaml: Dict[str, Any]):
    """Test validation fails with unsupported Python version."""
    yaml_path = tmp_path / "model.yaml"

    valid_model_yaml["python"]["version"] = "3.8"
    with open(yaml_path, "w") as f:
        yaml.dump(valid_model_yaml, f)

    with pytest.raises(BundleValidationError, match="Unsupported Python version: 3.8"):
        validate_model_yaml(yaml_path)


def test_validate_model_yaml_missing_python_version(tmp_path: Path, valid_model_yaml: Dict[str, Any]):
    """Test validation fails when python.version is missing."""
    yaml_path = tmp_path / "model.yaml"

    del valid_model_yaml["python"]["version"]
    with open(yaml_path, "w") as f:
        yaml.dump(valid_model_yaml, f)

    with pytest.raises(BundleValidationError, match="Missing required field: python.version"):
        validate_model_yaml(yaml_path)


def test_validate_model_yaml_missing_entrypoint_field(tmp_path: Path, valid_model_yaml: Dict[str, Any]):
    """Test validation fails when entrypoint fields are missing."""
    yaml_path = tmp_path / "model.yaml"

    del valid_model_yaml["entrypoint"]["module"]
    with open(yaml_path, "w") as f:
        yaml.dump(valid_model_yaml, f)

    with pytest.raises(BundleValidationError, match="Missing required entrypoint field: module"):
        validate_model_yaml(yaml_path)


def test_validate_model_yaml_unsupported_entrypoint_kind(tmp_path: Path, valid_model_yaml: Dict[str, Any]):
    """Test validation fails with unsupported entrypoint kind."""
    yaml_path = tmp_path / "model.yaml"

    valid_model_yaml["entrypoint"]["kind"] = "docker_container"
    with open(yaml_path, "w") as f:
        yaml.dump(valid_model_yaml, f)

    with pytest.raises(BundleValidationError, match="Unsupported entrypoint kind: docker_container"):
        validate_model_yaml(yaml_path)


def test_validate_model_yaml_missing_pip_requirements(tmp_path: Path, valid_model_yaml: Dict[str, Any]):
    """Test validation fails when pip_requirements is missing."""
    yaml_path = tmp_path / "model.yaml"

    del valid_model_yaml["environment"]["pip_requirements"]
    with open(yaml_path, "w") as f:
        yaml.dump(valid_model_yaml, f)

    with pytest.raises(BundleValidationError, match="Missing required field: environment.pip_requirements"):
        validate_model_yaml(yaml_path)


def test_validate_requirements_txt_success(tmp_path: Path):
    """Test validation of a properly pinned requirements.txt."""
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("""
numpy==1.24.3
scikit-learn==1.3.0
pandas==2.0.0
# Comment line
    """.strip())

    warnings = validate_requirements_txt(tmp_path, "requirements.txt")

    assert len(warnings) == 0


def test_validate_requirements_txt_missing_file(tmp_path: Path):
    """Test validation fails when requirements.txt doesn't exist."""
    with pytest.raises(BundleValidationError, match="requirements.txt not found"):
        validate_requirements_txt(tmp_path, "requirements.txt")


def test_validate_requirements_txt_unpinned_dependencies(tmp_path: Path):
    """Test warnings are generated for unpinned dependencies."""
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("""
numpy>=1.24.0
scikit-learn~=1.3.0
pandas
requests==2.31.0
    """.strip())

    warnings = validate_requirements_txt(tmp_path, "requirements.txt")

    assert len(warnings) == 3
    assert "Line 1" in warnings[0]
    assert "numpy>=1.24.0" in warnings[0]
    assert "Line 2" in warnings[1]
    assert "scikit-learn~=1.3.0" in warnings[1]
    assert "Line 3" in warnings[2]
    assert "pandas" in warnings[2]


def test_validate_requirements_txt_ignores_comments(tmp_path: Path):
    """Test that comments and blank lines are ignored."""
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("""
# This is a comment
numpy==1.24.3

# Another comment
scikit-learn==1.3.0
    """.strip())

    warnings = validate_requirements_txt(tmp_path, "requirements.txt")

    assert len(warnings) == 0


def test_validate_bundle_success(tmp_bundle_dir: Path, valid_model_yaml: Dict[str, Any]):
    """Test successful validation of a complete bundle."""
    # Write model.yaml
    yaml_path = tmp_bundle_dir / "model.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(valid_model_yaml, f)

    # Write requirements.txt
    req_file = tmp_bundle_dir / "requirements.txt"
    req_file.write_text("numpy==1.24.3\n")

    config = validate_bundle(tmp_bundle_dir)

    assert config["name"] == "test-model"
    assert config["version"] == "1.0.0"


def test_validate_bundle_missing_model_yaml(tmp_bundle_dir: Path):
    """Test validation fails when model.yaml is missing."""
    with pytest.raises(BundleValidationError, match="model.yaml not found"):
        validate_bundle(tmp_bundle_dir)


def test_validate_bundle_missing_requirements_txt(tmp_bundle_dir: Path, valid_model_yaml: Dict[str, Any]):
    """Test validation fails when requirements.txt is missing."""
    yaml_path = tmp_bundle_dir / "model.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(valid_model_yaml, f)

    with pytest.raises(BundleValidationError, match="requirements.txt not found"):
        validate_bundle(tmp_bundle_dir)


def test_validate_bundle_warns_missing_module(tmp_bundle_dir: Path, valid_model_yaml: Dict[str, Any]):
    """Test warning is issued when entrypoint module file is not found."""
    # Remove the runner.py file
    runner_file = tmp_bundle_dir / "src" / "test_bundle" / "runner.py"
    runner_file.unlink()

    yaml_path = tmp_bundle_dir / "model.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(valid_model_yaml, f)

    req_file = tmp_bundle_dir / "requirements.txt"
    req_file.write_text("numpy==1.24.3\n")

    # Should still succeed but with a warning (captured via stdout in practice)
    config = validate_bundle(tmp_bundle_dir)
    assert config is not None


def test_validate_bundle_with_artifacts(tmp_bundle_dir: Path, valid_model_yaml: Dict[str, Any]):
    """Test validation handles artifacts directory configuration."""
    valid_model_yaml["artifacts"] = {"root": "artifacts"}

    yaml_path = tmp_bundle_dir / "model.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(valid_model_yaml, f)

    req_file = tmp_bundle_dir / "requirements.txt"
    req_file.write_text("numpy==1.24.3\n")

    config = validate_bundle(tmp_bundle_dir)
    assert config["artifacts"]["root"] == "artifacts"


def test_validate_bundle_warns_missing_artifacts_dir(tmp_bundle_dir: Path, valid_model_yaml: Dict[str, Any]):
    """Test warning when configured artifacts directory doesn't exist."""
    # Remove artifacts directory
    artifacts_dir = tmp_bundle_dir / "artifacts"
    artifacts_dir.rmdir()

    valid_model_yaml["artifacts"] = {"root": "artifacts"}

    yaml_path = tmp_bundle_dir / "model.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(valid_model_yaml, f)

    req_file = tmp_bundle_dir / "requirements.txt"
    req_file.write_text("numpy==1.24.3\n")

    # Should still succeed but with a warning
    config = validate_bundle(tmp_bundle_dir)
    assert config is not None


def test_init_bundle_success(tmp_path: Path):
    """Test successful initialization of a new bundle."""
    bundle_name = "my-test-model"
    target_dir = tmp_path / bundle_name

    init_bundle(bundle_name, target_dir)

    # Check directory structure
    assert target_dir.exists()
    assert (target_dir / "model.yaml").exists()
    assert (target_dir / "requirements.txt").exists()
    assert (target_dir / "README.md").exists()
    assert (target_dir / "src" / "my_test_model").exists()
    assert (target_dir / "src" / "my_test_model" / "__init__.py").exists()
    assert (target_dir / "src" / "my_test_model" / "runner.py").exists()
    assert (target_dir / "artifacts").exists()

    # Check model.yaml content
    with open(target_dir / "model.yaml") as f:
        config = yaml.safe_load(f)

    assert config["name"] == bundle_name
    assert config["version"] == "0.1.0"
    assert config["schema_version"] == SCHEMA_VERSION
    assert config["python"]["version"] == "3.11"
    assert config["entrypoint"]["module"] == "src.my_test_model.runner"
    assert config["entrypoint"]["function"] == "run"

    # Check runner.py has the template code
    runner_content = (target_dir / "src" / "my_test_model" / "runner.py").read_text()
    assert "def run(nonce: int) -> float:" in runner_content
    assert "return 42.0" in runner_content


def test_init_bundle_default_directory(tmp_path: Path, monkeypatch):
    """Test initialization with default directory (cwd/name)."""
    monkeypatch.chdir(tmp_path)
    bundle_name = "my-model"

    init_bundle(bundle_name, target_dir=None)

    expected_dir = tmp_path / bundle_name
    assert expected_dir.exists()
    assert (expected_dir / "model.yaml").exists()


def test_init_bundle_existing_directory_error(tmp_path: Path):
    """Test initialization fails if target directory already exists."""
    bundle_name = "existing-model"
    target_dir = tmp_path / bundle_name
    target_dir.mkdir()

    with pytest.raises(SystemExit):
        init_bundle(bundle_name, target_dir)


def test_build_bundle_success(tmp_bundle_dir: Path, valid_model_yaml: Dict[str, Any]):
    """Test successful building of a bundle archive."""
    # Set up valid bundle
    yaml_path = tmp_bundle_dir / "model.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(valid_model_yaml, f)

    req_file = tmp_bundle_dir / "requirements.txt"
    req_file.write_text("numpy==1.24.3\n")

    output_dir = tmp_bundle_dir / "dist"

    build_bundle(tmp_bundle_dir, output_dir)

    # Check archive was created
    archive_name = f"{valid_model_yaml['name']}-{valid_model_yaml['version']}.tar.gz"
    archive_path = output_dir / archive_name

    assert archive_path.exists()
    assert archive_path.stat().st_size > 0

    # Verify archive contents
    with tarfile.open(archive_path, "r:gz") as tar:
        names = tar.getnames()

        # Check key files are included
        bundle_name = tmp_bundle_dir.name
        assert any("model.yaml" in name for name in names)
        assert any("requirements.txt" in name for name in names)
        assert any("runner.py" in name for name in names)


def test_build_bundle_default_output_directory(tmp_bundle_dir: Path, valid_model_yaml: Dict[str, Any]):
    """Test building with default output directory (./dist)."""
    yaml_path = tmp_bundle_dir / "model.yaml"
    with open(yaml_path, "w") as f:
        yaml.dump(valid_model_yaml, f)

    req_file = tmp_bundle_dir / "requirements.txt"
    req_file.write_text("numpy==1.24.3\n")

    build_bundle(tmp_bundle_dir, output_dir=None)

    default_output = tmp_bundle_dir / "dist"
    assert default_output.exists()

    archive_name = f"{valid_model_yaml['name']}-{valid_model_yaml['version']}.tar.gz"
    assert (default_output / archive_name).exists()


def test_build_bundle_validation_failure(tmp_bundle_dir: Path):
    """Test building fails when bundle validation fails."""
    # Don't create model.yaml - validation should fail
    with pytest.raises(BundleValidationError):
        build_bundle(tmp_bundle_dir)


def test_filter_tarfile_excludes_unwanted_files():
    """Test that _filter_tarfile excludes development and cache files."""
    import tarfile

    # Test excluded patterns - matches by directory name or exact filename
    excluded_patterns = [
        ".git/config",              # .git is in path.parts
        ".gitignore",               # Exact filename match
        "__pycache__/module.pyc",   # __pycache__ is in path.parts
        ".pytest_cache/v/cache",    # .pytest_cache is in path.parts
        ".mypy_cache/data.json",    # .mypy_cache is in path.parts
        ".tox/py311/lib",           # .tox is in path.parts
        ".venv/bin/python",         # .venv is in path.parts
        "venv/bin/python",          # venv is in path.parts
        ".env",                     # Exact filename match
        "dist/package.tar.gz",      # dist is in path.parts
        ".DS_Store",                # Exact filename match
    ]

    for pattern in excluded_patterns:
        info = tarfile.TarInfo(name=f"bundle/{pattern}")
        result = _filter_tarfile(info)
        assert result is None, f"Expected {pattern} to be filtered out"

    # Test included patterns
    included_patterns = [
        "model.yaml",
        "requirements.txt",
        "src/module.py",
        "artifacts/model.pkl",
        "README.md",
    ]

    for pattern in included_patterns:
        info = tarfile.TarInfo(name=f"bundle/{pattern}")
        result = _filter_tarfile(info)
        assert result is not None, f"Expected {pattern} to be included"


def test_filter_tarfile_excludes_by_directory_name():
    """Test that files within excluded directories are filtered."""
    info = tarfile.TarInfo(name="bundle/.git/objects/abc123")
    result = _filter_tarfile(info)
    assert result is None

    info = tarfile.TarInfo(name="bundle/__pycache__/module.cpython-311.pyc")
    result = _filter_tarfile(info)
    assert result is None


def test_supported_python_versions():
    """Test that SUPPORTED_PYTHON_VERSIONS contains expected versions."""
    assert "3.10" in SUPPORTED_PYTHON_VERSIONS
    assert "3.11" in SUPPORTED_PYTHON_VERSIONS
    assert "3.12" in SUPPORTED_PYTHON_VERSIONS
    assert "3.13" in SUPPORTED_PYTHON_VERSIONS
    assert len(SUPPORTED_PYTHON_VERSIONS) == 4


def test_schema_version():
    """Test that SCHEMA_VERSION is set correctly."""
    assert SCHEMA_VERSION == "0.1"


def test_validate_bundle_comprehensive(tmp_path: Path):
    """Test complete bundle validation with all features."""
    # Create comprehensive bundle structure
    bundle_dir = tmp_path / "full-bundle"
    bundle_dir.mkdir()

    # Create full directory structure
    src_dir = bundle_dir / "src" / "full_bundle"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("")
    (src_dir / "runner.py").write_text("def run(nonce: int) -> float:\n    return 42.0\n")

    artifacts_dir = bundle_dir / "artifacts"
    artifacts_dir.mkdir()
    (artifacts_dir / "model.pkl").write_text("dummy model data")

    # Create model.yaml with all optional fields
    config = {
        "schema_version": "0.1",
        "name": "full-bundle",
        "version": "1.0.0",
        "python": {
            "version": "3.12",
        },
        "entrypoint": {
            "kind": "python_function",
            "module": "src.full_bundle.runner",
            "function": "run",
            "workdir": ".",
            "artifacts_dir": "artifacts",
        },
        "environment": {
            "pip_requirements": "requirements.txt",
        },
        "artifacts": {
            "root": "artifacts",
        },
        "allora": {
            "topic_id": 69,
            "polling_interval": 120,
            "fee_tier": "STANDARD",
            "network": "testnet",
        },
    }

    with open(bundle_dir / "model.yaml", "w") as f:
        yaml.dump(config, f)

    # Create requirements.txt
    (bundle_dir / "requirements.txt").write_text("numpy==1.24.3\nscikit-learn==1.3.0\n")

    # Validate should succeed
    result = validate_bundle(bundle_dir)

    assert result["name"] == "full-bundle"
    assert result["python"]["version"] == "3.12"
    assert result["artifacts"]["root"] == "artifacts"
    assert result["allora"]["topic_id"] == 69
