"""EchoScope AI main Streamlit application entrypoint.

Delegates execution to the decoupled ui.app module.
"""

from ui.app import main

if __name__ == "__main__":
    main()
