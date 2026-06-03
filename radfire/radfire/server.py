"""Main entry point - runs Radicale CalDAV server with event consumer"""

import asyncio
import threading
from pathlib import Path

import radicale.config
from radicale.server import serve as radicale_serve

from .consumer import print_consumer


def get_config(data_dir: Path) -> radicale.config.Configuration:
    """Create Radicale configuration"""
    config = radicale.config.load()
    config.update({
        "server": {
            "hosts": "0.0.0.0:5232",
        },
        "storage": {
            "type": "radfire.storage",
            "filesystem_folder": str(data_dir / "collections"),
        },
        "auth": {
            "type": "htpasswd",
            "htpasswd_filename": str(data_dir / "htpasswd"),
            "htpasswd_encryption": "plain",
        },
        "logging": {
            "level": "info",
        },
    })
    return config


def run_radicale(config: radicale.config.Configuration) -> None:
    """Run Radicale server in a thread"""
    radicale_serve(config)


async def main(data_dir: Path | None = None) -> None:
    """Main entry point"""
    if data_dir is None:
        data_dir = Path.cwd() / "radfire_data"

    data_dir.mkdir(parents=True, exist_ok=True)
    print(f"Data directory: {data_dir}")

    config = get_config(data_dir)

    # Start Radicale in a separate thread (it's synchronous)
    radicale_thread = threading.Thread(
        target=run_radicale,
        args=(config,),
        daemon=True,
    )
    radicale_thread.start()
    print("Radicale CalDAV server started on http://0.0.0.0:5232")

    # Run the event consumer in the main async loop
    await print_consumer()


def cli() -> None:
    """CLI entry point"""
    asyncio.run(main())


if __name__ == "__main__":
    cli()
