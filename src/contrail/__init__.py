"""contrail: estimate and log the CO2e emissions of flights you've taken or booked."""

from importlib.metadata import PackageNotFoundError, version

# The distribution is `contrails`; the import package is `contrail`. They have
# differed since the PyPI release in 0.4.0, and asking for the wrong one does not
# raise anywhere a test would see it — it falls through to the dev version below,
# so `contrail --version` and the Passport footer quietly report 0.0.0.dev0.
try:
    __version__ = version("contrails")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
