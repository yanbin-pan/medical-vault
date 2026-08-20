"""The canonical store.

Everything in this package writes or reads plain files. No module here may
import the database layer — that dependency runs one way only, and keeping it
that way is what guarantees the vault stands alone.
"""

from medvault.vault.store import Vault, VaultDocument

__all__ = ["Vault", "VaultDocument"]
