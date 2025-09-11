import lazy_loader


__getattr__, __dir__, __all__ = lazy_loader.attach(
    __name__,
    submodules={
        'behavior',
        'camera',
        'experiments',
        'io',
        'projection',
        'utils',
    },
    submod_attrs={
        'behavior': [
            'FastTracker',
            'Fly',
            'fly_state',
            'process_contours',
            'tracker',
        ],
        'camera': [
            'AbstractCamera',
            'BaslerCamera',
            'base',
            'basler',
            'list_basler_cameras',
        ],
        'experiments': [
            'AbstractExperiment',
            'base',
        ],
        'io': [
            'led',
            'power',
        ],
        'projection': [
            'Artist',
            'Drawing',
            'artist',
            'drawing',
        ],
        'utils': [
            'AsyncVideoWriter',
            'Calibration',
            'calibration_loader',
            'config_loader',
            'deep_merge',
            'geometry',
            'load_config',
            'logging',
            'messaging',
            'numpy_encoder',
            'pack_frame',
            'pack_msg',
            'setup_process_logging',
            'transforms',
            'unpack_frame',
            'unpack_msg',
            'video',
        ],
    },
)

__all__ = ['AbstractCamera', 'AbstractExperiment', 'Artist',
           'AsyncVideoWriter', 'BaslerCamera', 'Calibration', 'Drawing',
           'FastTracker', 'Fly', 'artist', 'base', 'basler', 'behavior',
           'calibration_loader', 'camera', 'config_loader', 'deep_merge',
           'drawing', 'experiments', 'fly_state', 'geometry', 'io', 'led',
           'list_basler_cameras', 'load_config', 'logging', 'messaging',
           'numpy_encoder', 'pack_frame', 'pack_msg', 'power',
           'process_contours', 'projection', 'setup_process_logging',
           'tracker', 'transforms', 'unpack_frame', 'unpack_msg', 'utils',
           'video']
