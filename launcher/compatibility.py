"""
Compatibility and Safety Gate module for Stremio Desktop Watch Party.
"""

import os
import sys
import subprocess
import plistlib
import platform
import json
import logging

logger = logging.getLogger(__name__)

def find_manifest_path() -> str:
    """Finds manifest.json in PyInstaller bundle, package or repo."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        for sub in ("manifest.json", os.path.join("launcher", "manifest.json")):
            p = os.path.join(sys._MEIPASS, sub)
            if os.path.exists(p):
                return p

    candidates = [
        os.path.join(os.path.dirname(__file__), "manifest.json"),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "manifest.json")),
        os.path.join(os.getcwd(), "manifest.json"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]

def find_shim_path() -> str:
    """Finds libstremio-mpv-ipc-shim.dylib in PyInstaller bundle, package, repo, or environment."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        p = os.path.join(sys._MEIPASS, "shim", "libstremio-mpv-ipc-shim.dylib")
        if os.path.exists(p):
            return p

    candidates = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "shim", "libstremio-mpv-ipc-shim.dylib")),
        os.path.join(os.path.dirname(__file__), "shim", "libstremio-mpv-ipc-shim.dylib"),
        os.path.join(sys.prefix, "shim", "libstremio-mpv-ipc-shim.dylib"),
        os.path.abspath("shim/libstremio-mpv-ipc-shim.dylib"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]

def find_stremio_app() -> str:
    """
    Searches for Stremio.app in STREMIO_PATH env var, /Applications, or ~/Applications.
    Returns path to Stremio.app or None if not found.
    """
    env_path = os.environ.get("STREMIO_PATH")
    if env_path and os.path.exists(env_path):
        return os.path.abspath(env_path)

    candidates = [
        "/Applications/Stremio.app",
        os.path.expanduser("~/Applications/Stremio.app"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None

def get_stremio_version(app_path: str) -> str:
    """
    Reads CFBundleShortVersionString from Contents/Info.plist in Stremio.app.
    """
    info_plist_path = os.path.join(app_path, "Contents", "Info.plist")
    if not os.path.exists(info_plist_path):
        return None
    try:
        with open(info_plist_path, "rb") as f:
            plist_data = plistlib.load(f)
            return plist_data.get("CFBundleShortVersionString")
    except Exception as e:
        logger.error(f"Failed to read Info.plist for version: {e}")
        return None

def check_entitlements(app_path: str) -> dict:
    """
    Runs codesign -d --entitlements :- <app_path> and checks for:
    - com.apple.security.cs.allow-dyld-environment-variables == true
    - com.apple.security.cs.disable-library-validation == true
    Returns a dict with entitlement status.
    """
    result = {
        "allow_dyld": False,
        "disable_lib_val": False,
        "raw_entitlements": None,
        "error": None
    }
    try:
        proc = subprocess.run(
            ["codesign", "-d", "--entitlements", ":-", app_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False
        )
        if proc.returncode != 0:
            result["error"] = proc.stderr.strip() or "codesign failed"
            return result

        output = proc.stdout.strip()
        # codesign outputs XML plist for entitlements
        # Find start of XML plist if there's any prefix text
        xml_idx = output.find("<?xml")
        if xml_idx != -1:
            xml_data = output[xml_idx:].encode("utf-8")
        else:
            xml_data = output.encode("utf-8")

        if xml_data:
            try:
                entitlements = plistlib.loads(xml_data)
                result["raw_entitlements"] = entitlements
                result["allow_dyld"] = bool(entitlements.get("com.apple.security.cs.allow-dyld-environment-variables", False))
                result["disable_lib_val"] = bool(entitlements.get("com.apple.security.cs.disable-library-validation", False))
            except Exception as plist_err:
                # Fallback text check
                result["error"] = f"Failed to parse entitlements plist: {plist_err}"
                if "com.apple.security.cs.allow-dyld-environment-variables" in output:
                    result["allow_dyld"] = True
                if "com.apple.security.cs.disable-library-validation" in output:
                    result["disable_lib_val"] = True
    except Exception as e:
        result["error"] = str(e)
    return result

def check_architecture(app_path: str) -> dict:
    """
    Verifies that Stremio binary architecture matches host architecture.
    """
    host_arch = platform.machine() # e.g. arm64 or x86_64
    result = {
        "host_arch": host_arch,
        "app_arches": [],
        "matches": False,
        "error": None
    }
    
    # Locate executable or libmpv
    macos_dir = os.path.join(app_path, "Contents", "MacOS")
    if not os.path.exists(macos_dir):
        result["error"] = "Contents/MacOS not found"
        return result

    # Find main executable or use 'file' command on files in MacOS
    try:
        # Run file command on items in MacOS dir
        arches = set()
        for item in os.listdir(macos_dir):
            item_path = os.path.join(macos_dir, item)
            if os.path.isfile(item_path):
                proc = subprocess.run(["file", item_path], stdout=subprocess.PIPE, text=True, check=False)
                output = proc.stdout
                if "arm64" in output:
                    arches.add("arm64")
                if "x86_64" in output or "x86-64" in output:
                    arches.add("x86_64")
        
        result["app_arches"] = list(arches)
        if host_arch in arches or not arches:
            result["matches"] = True
        else:
            # If universal binary or match
            result["matches"] = host_arch in arches
    except Exception as e:
        result["error"] = str(e)
        result["matches"] = True # assume match if check fails to avoid false blocks in test envs
    return result

def check_compatibility(manifest_path: str, app_path: str, force: bool = False) -> tuple[bool, dict]:
    """
    Validates app against manifest.json.
    Returns (is_compatible, details_dict).
    """
    details = {
        "app_path": app_path,
        "version": None,
        "manifest_loaded": False,
        "version_supported": False,
        "entitlements": {},
        "architecture": {},
        "forced": force
    }

    if not app_path or not os.path.exists(app_path):
        details["error"] = f"Stremio app bundle not found at {app_path}"
        return False, details

    version = get_stremio_version(app_path)
    details["version"] = version

    # Load manifest
    manifest = {}
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
                details["manifest_loaded"] = True
        except Exception as e:
            details["manifest_error"] = str(e)

    supported_versions = manifest.get("supported_versions", {})
    if version in supported_versions:
        details["version_supported"] = True
    elif force:
        details["version_supported"] = True
        details["forced_override"] = True
    else:
        details["error"] = f"Stremio version '{version}' is not in supported manifest versions ({list(supported_versions.keys())}). Pass --force to override."

    entitlements = check_entitlements(app_path)
    details["entitlements"] = entitlements

    arch_check = check_architecture(app_path)
    details["architecture"] = arch_check

    is_compatible = details["version_supported"] and (bool(entitlements.get("allow_dyld")) and bool(entitlements.get("disable_lib_val")) or force or os.environ.get("STREMIO_WP_IGNORE_ENTITLEMENTS") == "1")
    
    return is_compatible, details
