import lazy_loader


__getattr__, __dir__, __all__ = lazy_loader.attach(
    __name__,
    submodules={
        'artist',
        'drawing',
    },
    submod_attrs={
        'artist': [
            'Artist',
        ],
        'drawing': [
            'Drawing',
        ],
    },
)

__all__ = ['Artist', 'Drawing', 'artist', 'drawing']
