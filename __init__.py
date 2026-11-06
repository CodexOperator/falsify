"""falsify, a Hermes Agent plugin rooted at this repository: Hermes imports this directory as a package
(hermes_cli/plugins_loader.py spec_from_file_location with submodule_search_locations), so the Hermes layer
in hermes_plugin/ reaches the core in falsify/ by relative import; no sys.path edits."""
from .hermes_plugin import register  # noqa: F401
