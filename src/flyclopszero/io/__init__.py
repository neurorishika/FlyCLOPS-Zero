import lazy_loader


__getattr__, __dir__, __all__ = lazy_loader.attach(
    __name__,
    submodules={
        'led',
        'power',
    },
    submod_attrs={},
)

__all__ = ['led', 'power']
