"""
Azure App Service entrypoint for gunicorn.

Startup command:
  gunicorn --bind=0.0.0.0 --timeout 600 --workers 1 --chdir /home/site/wwwroot startup_wrapper:app
"""

import os
import sys

# Ensure .python_packages is on the path (Azure zip deploy layout)
pkg_path = os.path.join(os.path.dirname(__file__), '.python_packages', 'lib', 'site-packages')
if os.path.isdir(pkg_path) and pkg_path not in sys.path:
    sys.path.insert(0, pkg_path)

from server import app  # noqa: E402
