import lazy_loader


__getattr__, __dir__, __all__ = lazy_loader.attach(
    __name__,
    submodules={
        'base',
    },
    submod_attrs={
        'base': [
            'AbstractExperiment',
        ],
    },
)

__all__ = ['AbstractExperiment', 'base']
