# Single source of truth for app version (must match docs / launchers).
APP_VERSION = "5.1.0"
APP_NAME = "smartLarch Platform"
APP_NAME_CN = "smartLarch Platform · Athena工作区 + Larch拟合"
# QSettings application name (no spaces). Legacy key kept for one-shot migration.
SETTINGS_ORG = "smartLarch"
SETTINGS_APP = "smartLarchPlatform"
SETTINGS_APP_LEGACY = "AutoLarchUI"
COPYRIGHT = "Copyright © 2026 by Dr. GENG Xun. Directly copy is prohibited."


_TEST_INI = None


def _test_settings_path() -> str:
    """One fresh throwaway INI per process, removed at exit (v4.4.0).

    A shared file let one interrupted run steer the next: a leaked
    ``ai_check_channel=cli`` sent test_coding_registry into a real modal box
    that never returns offscreen.
    """
    global _TEST_INI
    if _TEST_INI is None:
        import atexit
        import os
        import tempfile

        path = os.path.join(tempfile.gettempdir(), f"smartlarch_test_settings_{os.getpid()}.ini")
        if os.path.exists(path):
            os.remove(path)

        def _cleanup() -> None:
            try:
                os.remove(path)
            except OSError:
                pass

        atexit.register(_cleanup)
        _TEST_INI = path
    return _TEST_INI


def app_settings():
    """Return QSettings for this product, migrating keys from Auto LarchUI once.

    Headless runs (offscreen regression tests / CI) get a throwaway INI file:
    checkbox toggles in tests are wired to store.setValue and used to rewrite
    the user's live settings (v0.34.20: the suite silently flipped
    auto_reprocess / auto_edge_on_select off in the real registry).
    """
    import os

    from PySide6.QtCore import QSettings

    if os.environ.get("QT_QPA_PLATFORM") == "offscreen" or os.environ.get(
        "SMARTLARCH_TEST_SETTINGS"
    ):
        return QSettings(_test_settings_path(), QSettings.Format.IniFormat)
    store = QSettings(SETTINGS_ORG, SETTINGS_APP)
    if store.contains("lang") or store.allKeys():
        return store
    legacy = QSettings(SETTINGS_ORG, SETTINGS_APP_LEGACY)
    keys = legacy.allKeys()
    if not keys:
        return store
    for key in keys:
        store.setValue(key, legacy.value(key))
    store.sync()
    return store
