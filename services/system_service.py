"""Optional IP rotation (WireGuard 'wgcf'), used after YouTube blocks the server IP."""
import subprocess
from typing import Any, Dict


def rotate_ip() -> Dict[str, Any]:
    """Restart wg-quick@wgcf. Returns status 'error' when WireGuard isn't set up (e.g. Docker)."""
    try:
        result = subprocess.run(["sudo", "systemctl", "restart", "wg-quick@wgcf"],
                                capture_output=True, text=True, timeout=30, check=False)
        if result.returncode == 0:
            return {"status": "ok", "message": "IP rotated successfully", "ipv4": "unknown", "ipv6": "unknown"}
        return {"status": "error", "message": result.stderr or "Failed to rotate IP"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
