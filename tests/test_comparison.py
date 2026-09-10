from qualys_tracker.comparison import compare
from qualys_tracker.models import ChangeType, ModuleVersion


def test_first_run_all_new():
    current = [ModuleVersion("FIM-VERSION", "FIM", "1.0.0")]
    result = compare({}, current)
    assert len(result.new) == 1
    assert result.new[0].change_type == ChangeType.NEW_MODULE
    assert not result.changed
    assert not result.removed
    assert result.has_changes


def test_no_changes():
    previous = {"FIM": {"version": "1.0.0", "api_field": "FIM-VERSION"}}
    current = [ModuleVersion("FIM-VERSION", "FIM", "1.0.0")]
    result = compare(previous, current)
    assert not result.has_changes
    assert result.unchanged == ["FIM"]


def test_one_changed_module():
    previous = {"FIM": {"version": "1.0.0", "api_field": "FIM-VERSION"}}
    current = [ModuleVersion("FIM-VERSION", "FIM", "1.0.1")]
    result = compare(previous, current)
    assert len(result.changed) == 1
    change = result.changed[0]
    assert change.old_version == "1.0.0"
    assert change.new_version == "1.0.1"
    assert change.change_type == ChangeType.VERSION_CHANGED


def test_multiple_changed_modules():
    previous = {
        "FIM": {"version": "1.0.0", "api_field": "FIM-VERSION"},
        "CS": {"version": "1.44.5-9", "api_field": "CS-VERSION"},
    }
    current = [
        ModuleVersion("FIM-VERSION", "FIM", "1.0.1"),
        ModuleVersion("CS-VERSION", "CS", "1.45.0-1"),
    ]
    result = compare(previous, current)
    assert len(result.changed) == 2
    assert result.changed_count == 2


def test_new_module_added():
    previous = {"FIM": {"version": "1.0.0", "api_field": "FIM-VERSION"}}
    current = [
        ModuleVersion("FIM-VERSION", "FIM", "1.0.0"),
        ModuleVersion("WAF-VERSION", "WAF", "2.0.0"),
    ]
    result = compare(previous, current)
    assert len(result.new) == 1
    assert result.new[0].module == "WAF"
    assert result.unchanged == ["FIM"]


def test_module_removed():
    previous = {
        "FIM": {"version": "1.0.0", "api_field": "FIM-VERSION"},
        "WAF": {"version": "2.0.0", "api_field": "WAF-VERSION"},
    }
    current = [ModuleVersion("FIM-VERSION", "FIM", "1.0.0")]
    result = compare(previous, current)
    assert len(result.removed) == 1
    assert result.removed[0].module == "WAF"
    assert result.removed[0].change_type == ChangeType.MODULE_REMOVED
    assert result.removed[0].new_version is None


def test_combination_of_changes():
    previous = {
        "FIM": {"version": "1.0.0", "api_field": "FIM-VERSION"},
        "WAF": {"version": "2.0.0", "api_field": "WAF-VERSION"},
        "VM": {"version": "3.0.0", "api_field": "VM-VERSION"},
    }
    current = [
        ModuleVersion("FIM-VERSION", "FIM", "1.0.1"),  # changed
        ModuleVersion("VM-VERSION", "VM", "3.0.0"),  # unchanged
        ModuleVersion("CM-VERSION", "CM", "1.0.0"),  # new
        # WAF removed
    ]
    result = compare(previous, current)
    assert len(result.changed) == 1
    assert len(result.new) == 1
    assert len(result.removed) == 1
    assert len(result.unchanged) == 1
    assert result.has_changes


def test_non_semver_style_versions_are_only_string_compared():
    previous = {"CS": {"version": "2.12.0-12-101", "api_field": "CS-VERSION"}}
    current = [ModuleVersion("CS-VERSION", "CS", "2.9.0-1-5")]
    result = compare(previous, current)
    # Numerically this looks like a downgrade, but comparison is pure
    # string inequality -- it must still be reported as a change.
    assert len(result.changed) == 1
    assert result.changed[0].old_version == "2.12.0-12-101"
    assert result.changed[0].new_version == "2.9.0-1-5"
