from voxos.ui.app import run_ui
from voxos.config import get_settings
from voxos.core.sessions import get_sessions

if __name__ == '__main__':
    # Initialize settings and run UI
    settings = get_settings()
    # Continue the last conversation instead of a new random session each
    # launch (that is why Vox forgot the chat in every new terminal).
    session_id = get_sessions().current()
    run_ui(session_id)
