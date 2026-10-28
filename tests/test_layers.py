import importlib

import pytest

LAYERS = ["domain", "ports", "application", "adapters", "entrypoints"]


@pytest.mark.parametrize("layer", LAYERS)
def test_layer_package_imports(layer):
    importlib.import_module(f"agent_autonomy_lab.{layer}")
