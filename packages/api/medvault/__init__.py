"""Medical Vault: a durable, portable record of medical results.

The package is arranged around one idea. `medvault.vault` owns the canonical
store — append-only files that outlive this code. Everything else (the
SQLite projection, the API, the analytics) is derived and can be thrown
away and rebuilt from those files.
"""

__version__ = "0.1.0"
