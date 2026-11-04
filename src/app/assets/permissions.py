"""Apply destination ACL inheritance when publishing generated Asset files."""

import os
from pathlib import Path
import subprocess
import sys


def inherit_directory_permissions(directory):
    """Reset generated Windows files to their current parent's inherited ACLs."""
    if sys.platform != "win32":
        return
    executable = Path(os.environ["SystemRoot"]) / "System32" / "icacls.exe"
    try:
        subprocess.run(
            [str(executable), str(directory), "/reset", "/T", "/L", "/Q"],
            check=True, capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.CalledProcessError) as error:
        raise OSError(
            "Could not apply destination permissions to the extracted Asset.") from error
