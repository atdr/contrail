"""contrail: estimate and log the CO2e emissions of flights you've taken or booked."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("contrail")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
