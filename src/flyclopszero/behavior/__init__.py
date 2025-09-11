import lazy_loader


__getattr__, __dir__, __all__ = lazy_loader.attach(
    __name__,
    submodules={
        'fly_state',
        'tracker',
    },
    submod_attrs={
        'fly_state': [
            'Fly',
        ],
        'tracker': [
            'FastTracker',
            'process_contours',
        ],
    },
)

__all__ = ['FastTracker', 'Fly', 'fly_state', 'process_contours', 'tracker']
