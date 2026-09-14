import uuid
import sys
from voxos.ui.app import run_ui
from voxos.config import get_settings

if __name__ == '__main__':
    # Initialize settings and run UI
    settings = get_settings()
    session_id = str(uuid.uuid4())
    run_ui(session_id)
