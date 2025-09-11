import lazy_loader


__getattr__, __dir__, __all__ = lazy_loader.attach(
    __name__,
    submodules={
        'base',
        'basler',
    },
    submod_attrs={
        'base': [
            'AbstractCamera',
        ],
        'basler': [
            'BaslerCamera',
            'list_basler_cameras',
        ],
    },
)

__all__ = ['AbstractCamera', 'BaslerCamera', 'base', 'basler',
           'list_basler_cameras']
