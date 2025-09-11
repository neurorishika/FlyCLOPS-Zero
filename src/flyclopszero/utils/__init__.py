import lazy_loader


__getattr__, __dir__, __all__ = lazy_loader.attach(
    __name__,
    submodules={
        'calibration_loader',
        'config_loader',
        'geometry',
        'logging',
        'messaging',
        'transforms',
        'video',
    },
    submod_attrs={
        'calibration_loader': [
            'Calibration',
        ],
        'config_loader': [
            'deep_merge',
            'load_config',
        ],
        'logging': [
            'setup_process_logging',
        ],
        'messaging': [
            'numpy_encoder',
            'pack_frame',
            'pack_msg',
            'unpack_frame',
            'unpack_msg',
        ],
        'video': [
            'AsyncVideoWriter',
        ],
    },
)

__all__ = ['AsyncVideoWriter', 'Calibration', 'calibration_loader',
           'config_loader', 'deep_merge', 'geometry', 'load_config', 'logging',
           'messaging', 'numpy_encoder', 'pack_frame', 'pack_msg',
           'setup_process_logging', 'transforms', 'unpack_frame', 'unpack_msg',
           'video']
