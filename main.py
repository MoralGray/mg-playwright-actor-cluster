import asyncio

import uvicorn

from orc.server import create_app


def main() -> None:
    app = create_app()
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
