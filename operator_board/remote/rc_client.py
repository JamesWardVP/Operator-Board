"""Client for Unreal Engine's Remote Control HTTP API (Remote Control API plugin, port 30010).

Endpoints used here exist in every engine from 5.2 to 5.8.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

PYTHON_LIBRARY = "/Script/PythonScriptPlugin.Default__PythonScriptLibrary"
SYSTEM_LIBRARY = "/Script/Engine.Default__KismetSystemLibrary"
RESULT_MARKER = "OPB_RESULT:"


class RemoteControlError(Exception):
    pass


class RemoteControlClient:
    def __init__(self, host: str, port: int = 30010, timeout: float = 3.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise RemoteControlError(f"{method} {path} -> HTTP {exc.code} {detail[:300]}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise RemoteControlError(f"Remote Control unreachable at {self.base_url}: {exc}") from exc
        if not text.strip():
            return None
        try:
            return json.loads(text)
        except ValueError:
            return text

    # ---- core endpoints --------------------------------------------------------------
    def info(self) -> Any:
        return self.request("GET", "/remote/info")

    def ping(self) -> bool:
        try:
            self.info()
            return True
        except RemoteControlError:
            return False

    def call(self, object_path: str, function: str, parameters: Optional[Dict[str, Any]] = None,
             transaction: bool = False) -> Any:
        return self.request("PUT", "/remote/object/call", {
            "objectPath": object_path, "functionName": function,
            "parameters": parameters or {}, "generateTransaction": transaction})

    def get_property(self, object_path: str, prop: str) -> Any:
        return self.request("PUT", "/remote/object/property", {
            "objectPath": object_path, "propertyName": prop, "access": "READ_ACCESS"})

    def set_property(self, object_path: str, prop: str, value: Any, transaction: bool = True) -> Any:
        return self.request("PUT", "/remote/object/property", {
            "objectPath": object_path, "propertyName": prop,
            "access": "WRITE_TRANSACTION_ACCESS" if transaction else "WRITE_ACCESS",
            "propertyValue": {prop: value}})

    def describe(self, object_path: str) -> Any:
        return self.request("PUT", "/remote/object/describe", {"objectPath": object_path})

    def search_assets(self, query: str = "", class_names: Optional[List[str]] = None,
                      package_paths: Optional[List[str]] = None, limit: int = 500) -> List[Dict[str, Any]]:
        classes = class_names or []
        body = {"Query": query, "Limit": limit, "Filter": {
            "ClassNames": classes,
            "ClassPaths": [c if c.startswith("/") else f"/Script/Engine.{c}" for c in classes],
            "PackagePaths": package_paths or ["/Game"], "RecursiveClasses": True, "RecursivePaths": True}}
        result = self.request("PUT", "/remote/search/assets", body) or {}
        return list(result.get("Assets", [])) if isinstance(result, dict) else []

    # ---- presets (the "instance editable" surface exposed by the project) -------------
    def presets(self) -> List[Dict[str, Any]]:
        result = self.request("GET", "/remote/presets") or {}
        return list(result.get("Presets", [])) if isinstance(result, dict) else []

    def preset(self, name: str) -> Dict[str, Any]:
        result = self.request("GET", f"/remote/preset/{urllib.parse.quote(name)}") or {}
        return result.get("Preset", result) if isinstance(result, dict) else {}

    def get_preset_property(self, preset: str, label: str) -> Any:
        return self.request("GET", f"/remote/preset/{urllib.parse.quote(preset)}/property/{urllib.parse.quote(label)}")

    def set_preset_property(self, preset: str, label: str, value: Any) -> Any:
        return self.request("PUT", f"/remote/preset/{urllib.parse.quote(preset)}/property/{urllib.parse.quote(label)}",
                            {"PropertyValue": value, "GenerateTransaction": True})

    def call_preset_function(self, preset: str, label: str, parameters: Optional[Dict[str, Any]] = None) -> Any:
        return self.request("PUT", f"/remote/preset/{urllib.parse.quote(preset)}/function/{urllib.parse.quote(label)}",
                            {"Parameters": parameters or {}, "GenerateTransaction": True})

    # ---- commands -------------------------------------------------------------------
    def console_command(self, command: str) -> Any:
        return self.call(SYSTEM_LIBRARY, "ExecuteConsoleCommand", {"Command": command})

    def run_python(self, script: str) -> Any:
        """Run a Python script in the instance (requires the Python Editor Script Plugin).

        If the script prints a line beginning with OPB_RESULT:<json>, that JSON is returned.
        """
        result = self.call(PYTHON_LIBRARY, "ExecutePythonCommandEx", {
            "PythonCommand": script, "ExecutionMode": "ExecuteFile", "FileExecutionScope": "Private"})
        if not isinstance(result, dict):
            return result
        if result.get("ReturnValue") is False:
            raise RemoteControlError(_python_error(result))
        for entry in reversed(result.get("LogOutput", []) or []):
            text = str(entry.get("Output", "")) if isinstance(entry, dict) else str(entry)
            if text.startswith(RESULT_MARKER):
                try:
                    return json.loads(text[len(RESULT_MARKER):])
                except ValueError:
                    return text[len(RESULT_MARKER):]
        return result.get("CommandResult")


def _python_error(result: Dict[str, Any]) -> str:
    lines = [str(e.get("Output", "")) for e in result.get("LogOutput", []) or [] if isinstance(e, dict)]
    return "Python error in Unreal: " + ("\n".join(lines[-5:]) or str(result.get("CommandResult", "")))


def python_result_wrapper(body: str) -> str:
    """Wrap a script body so its `result` variable is printed back as JSON."""
    return (
        "import json, unreal\n"
        "result = None\n"
        "try:\n"
        + "".join(f"    {line}\n" for line in body.strip("\n").splitlines())
        + "except Exception as _e:\n"
        "    result = {'error': str(_e)}\n"
        f"print('{RESULT_MARKER}' + json.dumps(result, default=str))\n"
    )
