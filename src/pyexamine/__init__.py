"""pyExaMINE -- agent-based model of critical-minerals supply chains.

Import the pieces you need from the subpackages, e.g.::

    from pyexamine.model.supply_chain_model import MineralSupplyChainModel
    from pyexamine.config.lithium_config import LITHIUM_CONFIG
"""

from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("pyexamine")
except PackageNotFoundError:  # running from a checkout without an install
    __version__ = "0.0.0+unknown"
