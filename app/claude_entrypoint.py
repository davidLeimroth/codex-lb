"""Install the fork's additive router while retaining the pinned upstream runtime."""

from app.cli import main
from app.main import app
from app.modules.claude_messages.api import router

app.include_router(router)

if __name__ == "__main__":
    main()
