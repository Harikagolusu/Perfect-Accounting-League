"""Business logic layer.

Routers stay thin and delegate here. Intentionally empty until features land.
"""

from app.services.company import CompanyNotFoundError

__all__ = ["CompanyNotFoundError"]