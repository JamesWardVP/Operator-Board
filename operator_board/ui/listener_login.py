"""Password and security-key prompts for UE 5.4+ SwitchboardListeners.

The device manager publishes "device.auth" when a listener needs its password, rejects
it, or presents a different security key than last time. This asks once per machine
(several devices can share one listener), retries the device, and then connects the
other devices on that machine with the now-saved credential.
"""
from __future__ import annotations

from typing import Dict, List

from operator_board.qt import QtWidgets, exec_
from operator_board.ui.bridge import run_async


class ListenerLogin:
    def __init__(self, window) -> None:
        self.window = window
        self.ctx = window.ctx
        self._open: set = set()
        self._waiting: Dict[str, List[str]] = {}

    def handle(self, payload: dict) -> None:
        address = payload.get("address", "")
        names = self._waiting.setdefault(address, [])
        if payload.get("name") not in names:
            names.append(payload.get("name"))
        if address in self._open:
            return
        self._open.add(address)
        try:
            if payload.get("kind") == "fingerprint":
                self._ask_trust(address, payload)
            else:
                self._ask_password(address, payload)
        finally:
            self._open.discard(address)

    def _ask_password(self, address: str, payload: dict) -> None:
        subject = f" ({payload['subject']})" if payload.get("subject") else ""
        retry = ("That password was not accepted. Check it with 'Show password', or type it instead of "
                 "pasting.\n\n" if payload.get("kind") == "rejected" else "")
        text = (f"{retry}Enter the SwitchboardListener password for {address}{subject}.\n\n"
                f"Security key fingerprint: {payload.get('fingerprint') or 'unknown'}\n\n"
                "The password is set in the SwitchboardListener window on that machine (Change Password). "
                "It is saved in Windows Credential Manager and shared with Switchboard.")
        password = self._password_dialog(text)
        if not password:
            self._waiting.pop(address, None)
            self.window.notify(f"Not connected to {address}: no listener password entered", False)
            return
        self._retry(address, dict(password=password))

    def _password_dialog(self, text: str) -> str:
        dialog = QtWidgets.QDialog(self.window)
        dialog.setWindowTitle("SwitchboardListener password")
        dialog.setMinimumWidth(520)
        lay = QtWidgets.QVBoxLayout(dialog)
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        lay.addWidget(label)
        edit = QtWidgets.QLineEdit()
        edit.setEchoMode(QtWidgets.QLineEdit.Password)
        lay.addWidget(edit)
        show = QtWidgets.QCheckBox("Show password")
        show.toggled.connect(lambda on: edit.setEchoMode(
            QtWidgets.QLineEdit.Normal if on else QtWidgets.QLineEdit.Password))
        lay.addWidget(show)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        lay.addWidget(buttons)
        edit.setFocus()
        if exec_(dialog) != QtWidgets.QDialog.Accepted:
            return ""
        return edit.text()

    def _ask_trust(self, address: str, payload: dict) -> None:
        subject = f" ({payload['subject']})" if payload.get("subject") else ""
        box = QtWidgets.QMessageBox(self.window)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("Listener security key changed")
        box.setText(f"The SwitchboardListener at {address}{subject} presented a different security key than "
                    "last time.")
        box.setInformativeText(
            "Either the listener was reinstalled or its key was reset, or another computer is pretending to be "
            "this machine. If you did not expect this, check with whoever manages the stage before continuing."
            f"\n\nNew key fingerprint: {payload.get('fingerprint') or 'unknown'}")
        trust = box.addButton("Trust the new key", QtWidgets.QMessageBox.AcceptRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(QtWidgets.QMessageBox.Cancel)
        box.exec() if hasattr(box, "exec") else box.exec_()
        if box.clickedButton() is not trust:
            self._waiting.pop(address, None)
            self.window.notify(f"Not connected to {address}: security key not trusted", False)
            return
        self._retry(address, dict(trust_new_fingerprint=True))

    def _retry(self, address: str, kwargs: dict) -> None:
        names = self._waiting.pop(address, [])
        if not names:
            return
        first, others = names[0], names[1:]
        devices = self.ctx.devices

        def run() -> None:
            devices.connect(first, **kwargs)
            rt = devices.get(first)
            if rt is not None and rt.client is not None and rt.client.connected:
                for name in others:  # the credential is saved now
                    devices.connect(name)

        run_async(run)
