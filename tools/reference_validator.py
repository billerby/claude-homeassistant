#!/usr/bin/env python3
"""Entity and device reference validator for Home Assistant configuration files.

Validates that all entity references in configuration files actually exist.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, TypedDict

import yaml


class DomainSummary(TypedDict):
    """Type definition for domain summary dictionary."""

    count: int
    enabled: int
    disabled: int
    examples: List[str]


class HAYamlLoader(yaml.SafeLoader):
    """Custom YAML loader that handles Home Assistant specific tags."""

    pass


def include_constructor(loader, node):
    """Handle !include tag."""
    filename = loader.construct_scalar(node)
    return f"!include {filename}"


def include_dir_named_constructor(loader, node):
    """Handle !include_dir_named tag."""
    dirname = loader.construct_scalar(node)
    return f"!include_dir_named {dirname}"


def include_dir_merge_named_constructor(loader, node):
    """Handle !include_dir_merge_named tag."""
    dirname = loader.construct_scalar(node)
    return f"!include_dir_merge_named {dirname}"


def include_dir_merge_list_constructor(loader, node):
    """Handle !include_dir_merge_list tag."""
    dirname = loader.construct_scalar(node)
    return f"!include_dir_merge_list {dirname}"


def include_dir_list_constructor(loader, node):
    """Handle !include_dir_list tag."""
    dirname = loader.construct_scalar(node)
    return f"!include_dir_list {dirname}"


def input_constructor(loader, node):
    """Handle !input tag for blueprints."""
    input_name = loader.construct_scalar(node)
    return f"!input {input_name}"


def secret_constructor(loader, node):
    """Handle !secret tag."""
    secret_name = loader.construct_scalar(node)
    return f"!secret {secret_name}"


# Register custom constructors
HAYamlLoader.add_constructor("!include", include_constructor)
HAYamlLoader.add_constructor(
    "!include_dir_named", include_dir_named_constructor
)
HAYamlLoader.add_constructor(
    "!include_dir_merge_named", include_dir_merge_named_constructor
)
HAYamlLoader.add_constructor(
    "!include_dir_merge_list", include_dir_merge_list_constructor
)
HAYamlLoader.add_constructor("!include_dir_list", include_dir_list_constructor)
HAYamlLoader.add_constructor("!input", input_constructor)
HAYamlLoader.add_constructor("!secret", secret_constructor)


class ReferenceValidator:
    """Validates entity and device references in Home Assistant config."""

    # Special keywords that are not entity IDs
    SPECIAL_KEYWORDS = {"all", "none"}

    def __init__(self, config_dir: str = "config"):
        """Initialize the ReferenceValidator."""
        self.config_dir = Path(config_dir)
        self.storage_dir = self.config_dir / ".storage"
        self.errors: List[str] = []
        self.warnings: List[str] = []

        # Cache for loaded registries
        self._entities: Optional[Dict[str, Any]] = None
        self._devices: Optional[Dict[str, Any]] = None
        self._areas: Optional[Dict[str, Any]] = None

        # Cache for YAML-defined entities
        self._yaml_entities: Optional[Set[str]] = None

    def load_entity_registry(self) -> Dict[str, Any]:
        """Load and cache entity registry."""
        if self._entities is None:
            registry_file = self.storage_dir / "core.entity_registry"
            if not registry_file.exists():
                self.errors.append(f"Entity registry not found: {registry_file}")
                return {}

            try:
                with open(registry_file, "r") as f:
                    data = json.load(f)
                    self._entities = {
                        entity["entity_id"]: entity
                        for entity in data.get("data", {}).get("entities", [])
                    }
            except Exception as e:
                self.errors.append(f"Failed to load entity registry: {e}")
                return {}

        return self._entities

    def load_device_registry(self) -> Dict[str, Any]:
        """Load and cache device registry."""
        if self._devices is None:
            registry_file = self.storage_dir / "core.device_registry"
            if not registry_file.exists():
                self.errors.append(f"Device registry not found: {registry_file}")
                return {}

            try:
                with open(registry_file, "r") as f:
                    data = json.load(f)
                    self._devices = {
                        device["id"]: device
                        for device in data.get("data", {}).get("devices", [])
                    }
            except Exception as e:
                self.errors.append(f"Failed to load device registry: {e}")
                return {}

        return self._devices

    def load_area_registry(self) -> Dict[str, Any]:
        """Load and cache area registry."""
        if self._areas is None:
            registry_file = self.storage_dir / "core.area_registry"
            if not registry_file.exists():
                self.warnings.append(f"Area registry not found: {registry_file}")
                return {}

            try:
                with open(registry_file, "r") as f:
                    data = json.load(f)
                    self._areas = {
                        area["id"]: area
                        for area in data.get("data", {}).get("areas", [])
                    }
            except Exception as e:
                self.warnings.append(f"Failed to load area registry: {e}")
                return {}

        return self._areas

    def load_yaml_entities(self) -> Set[str]:
        """Load entities defined in YAML configuration files."""
        if self._yaml_entities is not None:
            return self._yaml_entities

        yaml_entities = set()

        # Parse configuration.yaml and extract YAML-defined entities
        for yaml_file in self.get_yaml_files():
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.load(f, Loader=HAYamlLoader)

                if data is None:
                    continue

                # Extract template sensors
                yaml_entities.update(self._extract_yaml_entities_from_config(data))

            except Exception:
                # Silently skip files that can't be parsed
                pass

        # Extract entities created by Python scripts
        yaml_entities.update(self._extract_python_script_entities())

        self._yaml_entities = yaml_entities
        return yaml_entities

    def _extract_python_script_entities(self) -> Set[str]:
        """Extract entities created by Python scripts."""
        entities = set()
        python_scripts_dir = self.config_dir / "python_scripts"

        if not python_scripts_dir.exists():
            return entities

        # Scan all .py files for hass.states.set() calls
        for script_file in python_scripts_dir.glob("*.py"):
            try:
                with open(script_file, "r", encoding="utf-8") as f:
                    content = f.read()

                # Look for hass.states.set('entity.id', ...) patterns
                import re
                patterns = [
                    r"hass\.states\.set\(['\"]([a-z_]+\.[a-z0-9_]+)['\"]",
                    r'hass\.states\.set\("([a-z_]+\.[a-z0-9_]+)"',
                ]

                for pattern in patterns:
                    matches = re.findall(pattern, content)
                    entities.update(matches)

            except Exception:
                # Skip files that can't be read
                pass

        return entities

    def _extract_yaml_entities_from_config(self, data: Any, domain: str = "") -> Set[str]:
        """Extract entity definitions from YAML config data."""
        entities = set()

        if not isinstance(data, dict):
            return entities

        # Handle different configuration styles
        for key, value in data.items():
            # Handle modern template: style as a list (e.g., template: - sensor: - binary_sensor:)
            if key == "template" and isinstance(value, list):
                for template_item in value:
                    if isinstance(template_item, dict):
                        for domain_key, domain_entities in template_item.items():
                            if domain_key in ["sensor", "binary_sensor"] and isinstance(domain_entities, list):
                                for entity_def in domain_entities:
                                    if isinstance(entity_def, dict) and "name" in entity_def:
                                        entity_name = entity_def["name"].lower().replace(" ", "_").replace('"', '').replace("-", "_")
                                        entities.add(f"{domain_key}.{entity_name}")

            # Platform-based sensors (legacy style)
            elif key in ["sensor", "binary_sensor", "template"] and isinstance(value, (list, dict)):
                current_domain = key if key != "template" else ""

                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            # Check for "name" field (direct entity definition)
                            if "name" in item:
                                entity_name = item["name"].lower().replace(" ", "_").replace('"', '').replace("-", "_")
                                entities.add(f"{current_domain or key}.{entity_name}")

                            # Check for platform key
                            platform = item.get("platform")
                            if platform == "template":
                                # Extract sensors from template platform
                                entities.update(self._extract_template_entities(item, current_domain or "sensor"))
                            # Check for direct entity definitions in sensors list
                            entities.update(self._extract_template_entities(item, current_domain or key))

                elif isinstance(value, dict):
                    # Modern template style or direct sensor definitions
                    if key == "template":
                        # New template: style with sensor/binary_sensor subsections
                        for template_key, template_value in value.items():
                            if template_key in ["sensor", "binary_sensor"] and isinstance(template_value, list):
                                for entity_def in template_value:
                                    if isinstance(entity_def, dict) and "name" in entity_def:
                                        entity_name = entity_def["name"].lower().replace(" ", "_").replace('"', '').replace("-", "_")
                                        entities.add(f"{template_key}.{entity_name}")
                    else:
                        # Legacy sensor: or binary_sensor: with direct entity definitions
                        entities.update(self._extract_template_entities(value, key))

            # Handle integration-based entities (mqtt, rest, etc.) with sensor/binary_sensor subsections
            elif key in ["mqtt", "rest", "command_line", "sql", "scrape"] and isinstance(value, dict):
                # These integrations can have sensor/binary_sensor subsections
                for sub_key, sub_value in value.items():
                    if sub_key in ["sensor", "binary_sensor"] and isinstance(sub_value, list):
                        for entity_def in sub_value:
                            if isinstance(entity_def, dict) and "name" in entity_def:
                                entity_name = entity_def["name"].lower().replace(" ", "_").replace('"', '').replace("-", "_")
                                entities.add(f"{sub_key}.{entity_name}")

            # Handle input_* helpers
            elif key in ["input_number", "input_boolean", "input_text", "input_select", "input_datetime"] and isinstance(value, dict):
                for entity_name in value.keys():
                    entities.add(f"{key}.{entity_name}")

        return entities

    def _extract_template_entities(self, config: Any, domain: str) -> Set[str]:
        """Extract entity IDs from template sensor configuration."""
        entities = set()

        if not isinstance(config, dict):
            return entities

        # Look for sensors defined directly in the config
        for key, value in config.items():
            if isinstance(value, dict):
                # Check for legacy "sensors:" or "binary_sensors:" subsection in platform: template
                if key in ["sensors", "binary_sensors"]:
                    # Determine the domain from the key
                    entity_domain = "binary_sensor" if key == "binary_sensors" else domain
                    # Extract entities from the subsection
                    for entity_name, entity_config in value.items():
                        if isinstance(entity_config, dict):
                            entity_id = f"{entity_domain}.{entity_name}"
                            entities.add(entity_id)
                # Check if this looks like an entity definition
                elif "friendly_name" in value or "value_template" in value or "state" in value:
                    # This is an entity definition, key is the entity name
                    entity_id = f"{domain}.{key}"
                    entities.add(entity_id)
                elif key in ["sensor", "binary_sensor"]:
                    # Nested sensor definitions
                    entities.update(self._extract_template_entities(value, key))

        return entities

    def is_uuid_format(self, value: str) -> bool:
        """Check if a string matches UUID format (32 hex characters)."""
        # UUID format: 8-4-4-4-12 hex digits, but HA often stores without hyphens
        uuid_pattern = r"^[a-f0-9]{32}$"
        return bool(re.match(uuid_pattern, value))

    def is_template(self, value: str) -> bool:
        """Check if value is a Jinja2 template expression."""
        # Match template expressions like {{ ... }}
        return bool(re.search(r"\{\{.*?\}\}", value))

    def should_skip_entity_validation(self, value: str) -> bool:
        """Check if entity reference should be skipped during validation."""
        return (
            value.startswith("!")
            or self.is_uuid_format(value)  # HA tags like !input, !secret
            or self.is_template(value)  # UUID format (device-based)
            or value  # Template expressions
            in self.SPECIAL_KEYWORDS  # Special keywords like "all", "none"
        )

    def extract_entity_references(self, data: Any, path: str = "") -> Set[str]:
        """Extract entity references from configuration data."""
        entities = set()

        if isinstance(data, dict):
            for key, value in data.items():
                current_path = f"{path}.{key}" if path else key

                # Common entity reference keys
                if key in ["entity_id", "entity_ids", "entities"]:
                    if isinstance(value, str):
                        if not self.should_skip_entity_validation(value):
                            entities.add(value)
                    elif isinstance(value, list):
                        for entity in value:
                            if isinstance(
                                entity, str
                            ) and not self.should_skip_entity_validation(entity):
                                entities.add(entity)

                # Device-related keys
                elif key in ["device_id", "device_ids"]:
                    # Device IDs are handled separately
                    pass

                # Area-related keys
                elif key in ["area_id", "area_ids"]:
                    # Area IDs are handled separately
                    pass

                # Service data might contain entity references
                elif key == "data" and isinstance(value, dict):
                    entities.update(self.extract_entity_references(value, current_path))

                # Templates might contain entity references
                elif isinstance(value, str) and any(
                    x in value for x in ["state_attr(", "states(", "is_state("]
                ):
                    entities.update(self.extract_entities_from_template(value))

                # Recursive search
                else:
                    entities.update(self.extract_entity_references(value, current_path))

        elif isinstance(data, list):
            for i, item in enumerate(data):
                current_path = f"{path}[{i}]" if path else f"[{i}]"
                entities.update(self.extract_entity_references(item, current_path))

        return entities

    def extract_entities_from_template(self, template: str) -> Set[str]:
        """Extract entity references from Jinja2 templates."""
        entities = set()

        # Common patterns for entity references in templates
        patterns = [
            r"states\('([^']+)'\)",  # states('entity.id')
            r'states\("([^"]+)"\)',  # states("entity.id")
            # states.domain.entity
            r"states\.([a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*)",
            r"is_state\('([^']+)'",  # is_state('entity.id', ...)
            r'is_state\("([^"]+)"',  # is_state("entity.id", ...)
            r"state_attr\('([^']+)'",  # state_attr('entity.id', ...)
            r'state_attr\("([^"]+)"',  # state_attr("entity.id", ...)
        ]

        for pattern in patterns:
            matches = re.findall(pattern, template)
            for match in matches:
                # Validate entity ID format
                if "." in match and len(match.split(".")) == 2:
                    entities.add(match)

        return entities

    def extract_device_references(self, data: Any) -> Set[str]:
        """Extract device references from configuration data."""
        devices = set()

        if isinstance(data, dict):
            for key, value in data.items():
                if key in ["device_id", "device_ids"]:
                    if isinstance(value, str):
                        # Skip blueprint inputs and other HA tags
                        if not value.startswith("!"):
                            devices.add(value)
                    elif isinstance(value, list):
                        for device in value:
                            if isinstance(device, str) and not device.startswith("!"):
                                devices.add(device)
                else:
                    devices.update(self.extract_device_references(value))

        elif isinstance(data, list):
            for item in data:
                devices.update(self.extract_device_references(item))

        return devices

    def extract_area_references(self, data: Any) -> Set[str]:
        """Extract area references from configuration data."""
        areas = set()

        if isinstance(data, dict):
            for key, value in data.items():
                if key in ["area_id", "area_ids"]:
                    if isinstance(value, str):
                        # Skip blueprint inputs and other HA tags
                        if not value.startswith("!"):
                            areas.add(value)
                    elif isinstance(value, list):
                        for area in value:
                            if isinstance(area, str) and not area.startswith("!"):
                                areas.add(area)
                else:
                    areas.update(self.extract_area_references(value))

        elif isinstance(data, list):
            for item in data:
                areas.update(self.extract_area_references(item))

        return areas

    def extract_entity_registry_ids(self, data: Any) -> Set[str]:
        """Extract entity registry UUID references from configuration data."""
        entity_registry_ids = set()

        if isinstance(data, dict):
            for key, value in data.items():
                # Look for entity_id fields containing UUIDs (device-based automations)
                if key == "entity_id" and isinstance(value, str):
                    if self.is_uuid_format(value):
                        entity_registry_ids.add(value)
                else:
                    entity_registry_ids.update(self.extract_entity_registry_ids(value))
        elif isinstance(data, list):
            for item in data:
                entity_registry_ids.update(self.extract_entity_registry_ids(item))

        return entity_registry_ids

    def get_entity_registry_id_mapping(self) -> Dict[str, str]:
        """Get mapping from entity registry ID to entity_id."""
        entities = self.load_entity_registry()
        return {
            entity_data["id"]: entity_data["entity_id"]
            for entity_data in entities.values()
            if "id" in entity_data
        }

    def validate_file_references(self, file_path: Path) -> bool:
        """Validate all references in a single file."""
        if file_path.name == "secrets.yaml":
            return True  # Skip secrets file

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.load(f, Loader=HAYamlLoader)
        except Exception as e:
            self.errors.append(f"{file_path}: Failed to load YAML - {e}")
            return False

        if data is None:
            return True  # Empty file is valid

        # Extract references
        entity_refs = self.extract_entity_references(data)
        device_refs = self.extract_device_references(data)
        area_refs = self.extract_area_references(data)
        entity_registry_ids = self.extract_entity_registry_ids(data)

        # Load registries
        entities = self.load_entity_registry()
        devices = self.load_device_registry()
        areas = self.load_area_registry()
        entity_id_mapping = self.get_entity_registry_id_mapping()

        all_valid = True

        # Load YAML-defined entities
        yaml_entities = self.load_yaml_entities()

        # Validate entity references (normal entity_id format)
        for entity_id in entity_refs:
            # Skip UUID-format entity IDs, they're handled separately
            if self.is_uuid_format(entity_id):
                continue

            if entity_id not in entities:
                # Check if it's a disabled entity
                disabled_entities = {
                    e["entity_id"]: e
                    for e in entities.values()
                    if e.get("disabled_by") is not None
                }

                if entity_id in disabled_entities:
                    self.warnings.append(
                        f"{file_path}: References disabled entity " f"'{entity_id}'"
                    )
                elif entity_id in yaml_entities:
                    # Entity is defined in YAML config, not an error
                    self.warnings.append(
                        f"{file_path}: References YAML-defined entity '{entity_id}' "
                        f"(not in entity registry)"
                    )
                else:
                    self.errors.append(f"{file_path}: Unknown entity '{entity_id}'")
                    all_valid = False

        # Validate entity registry ID references (UUID format)
        for registry_id in entity_registry_ids:
            if registry_id not in entity_id_mapping:
                self.errors.append(
                    f"{file_path}: Unknown entity registry ID '{registry_id}'"
                )
                all_valid = False
            else:
                # Check if the mapped entity is disabled
                actual_entity_id = entity_id_mapping[registry_id]
                if actual_entity_id in entities:
                    entity_data = entities[actual_entity_id]
                    if entity_data.get("disabled_by") is not None:
                        self.warnings.append(
                            f"{file_path}: Entity registry ID '{registry_id}' "
                            f"references disabled entity '{actual_entity_id}'"
                        )

        # Validate device references
        for device_id in device_refs:
            if device_id not in devices:
                self.errors.append(f"{file_path}: Unknown device '{device_id}'")
                all_valid = False

        # Validate area references
        for area_id in area_refs:
            if area_id not in areas:
                self.warnings.append(f"{file_path}: Unknown area '{area_id}'")

        return all_valid

    def get_yaml_files(self) -> List[Path]:
        """Get all YAML files to validate."""
        yaml_files: List[Path] = []
        for pattern in ["*.yaml", "*.yml"]:
            yaml_files.extend(self.config_dir.glob(pattern))

        # Skip blueprints directory - these are templates with !input tags
        return yaml_files

    def validate_all(self) -> bool:
        """Validate all references in the config directory."""
        if not self.config_dir.exists():
            self.errors.append(f"Config directory {self.config_dir} does not exist")
            return False

        yaml_files = self.get_yaml_files()
        if not yaml_files:
            self.warnings.append("No YAML files found in config directory")
            return True

        all_valid = True

        for file_path in yaml_files:
            if not self.validate_file_references(file_path):
                all_valid = False

        return all_valid

    def get_entity_summary(self) -> Dict[str, DomainSummary]:
        """Get summary of available entities by domain."""
        entities = self.load_entity_registry()

        summary: Dict[str, DomainSummary] = {}
        for entity_id, entity_data in entities.items():
            domain = entity_id.split(".")[0]
            if domain not in summary:
                summary[domain] = {
                    "count": 0,
                    "enabled": 0,
                    "disabled": 0,
                    "examples": [],
                }

            summary[domain]["count"] += 1
            if entity_data.get("disabled_by") is None:
                summary[domain]["enabled"] += 1
            else:
                summary[domain]["disabled"] += 1

            # Add some examples
            if len(summary[domain]["examples"]) < 3:
                summary[domain]["examples"].append(entity_id)

        return summary

    def print_results(self):
        """Print validation results."""
        if self.errors:
            print("ERRORS:")
            for error in self.errors:
                print(f"  ❌ {error}")
            print()

        if self.warnings:
            print("WARNINGS:")
            for warning in self.warnings:
                print(f"  ⚠️  {warning}")
            print()

        # Print entity summary
        summary = self.get_entity_summary()
        if summary:
            print("AVAILABLE ENTITIES BY DOMAIN:")
            for domain, info in sorted(summary.items()):
                enabled_count = info["enabled"]
                disabled_count = info["disabled"]
                print(
                    f"  {domain}: {enabled_count} enabled, "
                    f"{disabled_count} disabled"
                )
                if info["examples"]:
                    print(f"    Examples: {', '.join(info['examples'])}")
            print()

        if not self.errors and not self.warnings:
            print("✅ All entity/device references are valid!")
        elif not self.errors:
            print("✅ Entity/device references are valid (with warnings)")
        else:
            print("❌ Invalid entity/device references found")


def main():
    """Run entity and device reference validation from command line."""
    config_dir = sys.argv[1] if len(sys.argv) > 1 else "config"

    validator = ReferenceValidator(config_dir)
    is_valid = validator.validate_all()
    validator.print_results()

    sys.exit(0 if is_valid else 1)


if __name__ == "__main__":
    main()
