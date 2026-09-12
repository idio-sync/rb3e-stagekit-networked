"""Desktop dashboard composition with lazy public access."""

__all__ = ["RB3Dashboard"]


def __getattr__(name):
    if name == "RB3Dashboard":
        from rb3dashboard.desktop.app import RB3Dashboard
        return RB3Dashboard
    raise AttributeError(name)
